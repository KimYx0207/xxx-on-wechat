import os
import re
import threading
import time
from asyncio import CancelledError
from concurrent.futures import Future, ThreadPoolExecutor
import requests
import json
import uuid

from bridge.context import *
from bridge.reply import *
from channel.channel import Channel
from common.dequeue import Dequeue
from common import memory
from plugins import *
from database.group_members_db import get_group_member_from_db, save_group_members_to_db

try:
    from voice.audio_convert import any_to_wav
except Exception as e:
    pass

handler_pool = ThreadPoolExecutor(max_workers=8)  # 处理消息的线程池

def get_group_member_display_name(group_id, wxid, bot_wxid=None, api_base_url=None):
    """
    获取群成员的@名称，优先DisplayName，无则NickName。
    1. 先查本地sqlite缓存。
    2. 查不到则请求接口并缓存。
    """
    import logging
    # 统一提前import，保证作用域
    try:
        from xbot.database.group_members_db import get_group_member_from_db, save_group_members_to_db
    except ImportError:
        from database.group_members_db import get_group_member_from_db, save_group_members_to_db
    # 1. 本地缓存
    try:
        member = get_group_member_from_db(group_id, wxid)
        if member:
            logger.info(f"[get_group_member_display_name] 本地缓存命中: group_id={group_id}, wxid={wxid}, display_name={member.get('display_name')}, nickname={member.get('nickname')}")
            return member.get("display_name") or member.get("nickname")
        else:
            logger.info(f"[get_group_member_display_name] 本地缓存未命中: group_id={group_id}, wxid={wxid}")
    except Exception as e:
        logger.warning(f"[get_group_member_display_name] 本地缓存查询异常: {e}")
    # 2. 请求接口
    try:
        # 自动获取api_base_url和bot_wxid
        if api_base_url is None:
            from config import conf
            api_base_url = conf().get("xbot_base_url") or conf().get("gewechat_base_url")
        if bot_wxid is None:
            # 直接用相对路径查找根目录下resource/robot_stat.json（修正为上一级目录）
            try:
                resource_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../resource/robot_stat.json'))
                logger.info(f"[get_group_member_display_name] 直接用相对路径读取robot_stat.json: {resource_path}")
                with open(resource_path, "r", encoding="utf-8") as f:
                    stat = json.load(f)
                    bot_wxid = stat.get("wxid")
                    logger.info(f"[get_group_member_display_name] 从robot_stat.json获取bot_wxid: {bot_wxid}")
            except Exception as e:
                logger.error(f"[get_group_member_display_name] 读取robot_stat.json失败: {e}")
        if not api_base_url or not bot_wxid:
            logger.error(f"[get_group_member_display_name] 缺少api_base_url或bot_wxid, api_base_url={api_base_url}, bot_wxid={bot_wxid}")
            return None
        url = api_base_url.rstrip("/") + "/Group/GetChatRoomMemberDetail"
        payload = {"QID": group_id, "Wxid": bot_wxid}
        logger.info(f"[get_group_member_display_name] 请求接口: url={url}, payload={payload}")
        resp = requests.post(url, json=payload, timeout=5)
        logger.info(f"[get_group_member_display_name] 接口响应: status={resp.status_code}, text={resp.text[:200]}")
        data = resp.json()
        members = data.get("Data", {}).get("NewChatroomData", {}).get("ChatRoomMember", [])
        if members:
            logger.info(f"[get_group_member_display_name] 接口返回成员数: {len(members)}，写入本地缓存")
            save_group_members_to_db(group_id, members)
            for member in members:
                member_wxid = member.get("UserName") or member.get("wxid")
                if member_wxid == wxid:
                    logger.info(f"[get_group_member_display_name] 命中接口成员: display_name={member.get('DisplayName')}, nickname={member.get('NickName')}")
                    return member.get("DisplayName") or member.get("NickName")
            logger.warning(f"[get_group_member_display_name] 接口返回但未找到wxid: {wxid}")
        else:
            logger.warning(f"[get_group_member_display_name] 接口返回成员为空: {data}")
    except Exception as e:
        logger.exception(f"[get_group_member_display_name] 接口请求异常: {e}")
    return None

def download_image_to_tmp(url):
    tmp_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../resource/tmp"))
    os.makedirs(tmp_dir, exist_ok=True)
    ext = os.path.splitext(url)[-1]
    if not ext or len(ext) > 5:
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(tmp_dir, filename)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(resp.content)
        return save_path
    except Exception as e:
        logger.error(f"[download_image_to_tmp] 下载图片失败: {e}")
        return None

# 抽象类, 它包含了与消息通道无关的通用处理逻辑
class ChatChannel(Channel):
    name = None  # 登录的用户名
    user_id = None  # 登录的用户id
    futures = {}  # 记录每个session_id提交到线程池的future对象, 用于重置会话时把没执行的future取消掉，正在执行的不会被取消
    sessions = {}  # 用于控制并发，每个session_id同时只能有一个context在处理
    lock = threading.Lock()  # 用于控制对sessions的访问

    def __init__(self):
        _thread = threading.Thread(target=self.consume)
        _thread.setDaemon(True)
        _thread.start()

    # 根据消息构造context，消息内容相关的触发项写在这里
    def _compose_context(self, ctype: ContextType, content, **kwargs):
        # gewechat/gewe风格兜底过滤：非用户消息直接 return None
        cmsg = kwargs.get("msg")
        if cmsg and hasattr(cmsg, "_is_non_user_message") and cmsg._is_non_user_message(getattr(cmsg, "msg_source", ""), getattr(cmsg, "from_user_id", "")):
            logger.info(f"[chat_channel] ignore non-user/system message in _compose_context: from={getattr(cmsg, 'from_user_id', '')}")
            return None
        context = Context(ctype, content)
        context.kwargs = kwargs
        if ctype == ContextType.ACCEPT_FRIEND:
            return context
        # context首次传入时，origin_ctype是None,
        # 引入的起因是：当输入语音时，会嵌套生成两个context，第一步语音转文本，第二步通过文本生成文字回复。
        # origin_ctype用于第二步文本回复时，判断是否需要匹配前缀，如果是私聊的语音，就不需要匹配前缀
        if "origin_ctype" not in context:
            context["origin_ctype"] = ctype
        # context首次传入时，receiver是None，根据类型设置receiver
        first_in = "receiver" not in context
        # 群名匹配过程，设置session_id和receiver
        if first_in:  # context首次传入时，receiver是None，根据类型设置receiver
            config = conf()
            cmsg = context["msg"]
            user_data = conf().get_user_data(cmsg.from_user_id)
            context["openai_api_key"] = user_data.get("openai_api_key")
            context["gpt_model"] = user_data.get("gpt_model")
            if context.get("isgroup", False):
                group_name = cmsg.other_user_nickname
                group_id = cmsg.other_user_id
                context["group_name"] = group_name

                group_name_white_list = config.get("group_name_white_list", [])
                group_name_keyword_white_list = config.get("group_name_keyword_white_list", [])
                if any(
                        [
                            group_name in group_name_white_list,
                            "ALL_GROUP" in group_name_white_list,
                            check_contain(group_name, group_name_keyword_white_list),
                        ]
                ):
                    group_chat_in_one_session = conf().get("group_chat_in_one_session", [])
                    session_id = f"{cmsg.actual_user_id}@@{group_id}" # 当群聊未共享session时，session_id为user_id与group_id的组合，用于区分不同群聊以及单聊
                    context["is_shared_session_group"] = False  # 默认为非共享会话群
                    if any(
                            [
                                group_name in group_chat_in_one_session,
                                "ALL_GROUP" in group_chat_in_one_session,
                            ]
                    ):
                        session_id = group_id
                        context["is_shared_session_group"] = True  # 如果是共享会话群，设置为True
                else:
                    logger.debug(f"No need reply, groupName not in whitelist, group_name={group_name}")
                    return None
                context["session_id"] = session_id
                context["receiver"] = group_id
            else:
                context["session_id"] = cmsg.other_user_id
                context["receiver"] = cmsg.other_user_id
            e_context = PluginManager().emit_event(EventContext(Event.ON_RECEIVE_MESSAGE, {"channel": self, "context": context}))
            context = e_context["context"]
            if e_context.is_pass() or context is None:
                return context
            if cmsg.from_user_id == self.user_id and not config.get("trigger_by_self", True):
                logger.debug("[chat_channel]self message skipped")
                return None

        # 消息内容匹配过程，并处理content
        if ctype == ContextType.TEXT:
            nick_name_black_list = conf().get("nick_name_black_list", [])
            if context.get("isgroup", False):  # 群聊
                # gewechat/gewe风格：实际发言人是自己，直接 return None
                if context["msg"].actual_user_id == self.user_id or context["msg"].from_user_id == self.user_id:
                    logger.debug(f"[chat_channel] skip self message in group: actual_user_id={context['msg'].actual_user_id}, self_user_id={self.user_id}")
                    return None
                match_prefix = check_prefix(content, conf().get("group_chat_prefix"))
                match_contain = check_contain(content, conf().get("group_chat_keyword"))
                logger.debug(f"[chat_channel] group check: content={content}, match_prefix={match_prefix}, match_contain={match_contain}, is_at={context['msg'].is_at}")
                flag = False
                if match_prefix is not None or match_contain is not None:
                    flag = True
                    if match_prefix:
                        content = content.replace(match_prefix, "", 1).strip()
                if context["msg"].is_at:
                    nick_name = context["msg"].actual_user_nickname
                    nick_name_black_list = conf().get("nick_name_black_list", [])
                    if nick_name and nick_name in nick_name_black_list:
                        logger.warning(f"[chat_channel] Nickname {nick_name} in In BlackList, ignore")
                        return None
                    logger.info("[chat_channel]receive group at")
                    if not conf().get("group_at_off", False):
                        flag = True
                    self.name = self.name if self.name is not None else ""  # 部分渠道self.name可能没有赋值
                    pattern = f"@{re.escape(self.name)}(\u2005|\u0020)"
                    subtract_res = re.sub(pattern, r"", content)
                    if isinstance(context["msg"].at_list, list):
                        for at in context["msg"].at_list:
                            pattern = f"@{re.escape(at)}(\u2005|\u0020)"
                            subtract_res = re.sub(pattern, r"", subtract_res)
                    if subtract_res == content and context["msg"].self_display_name:
                        pattern = f"@{re.escape(context['msg'].self_display_name)}(\u2005|\u0020)"
                        subtract_res = re.sub(pattern, r"", content)
                    content = subtract_res
                    
                    # 新增：彻底清理所有@前缀，确保传递给插件的是干净的命令
                    content = re.sub(r"^@\S+\s+", "", content)
                    logger.debug(f"[chat_channel] after cleaning all @ prefixes: {content}")
                    
                if not flag:
                    logger.debug(f"[chat_channel] group message not match any produce condition, skip. content={content}")
                    return None
            else:  # 单聊
                nick_name = context["msg"].from_user_nickname
                if nick_name and nick_name in nick_name_black_list:
                    # 黑名单过滤
                    logger.warning(f"[chat_channel] Nickname '{nick_name}' in In BlackList, ignore")
                    return None

                match_prefix = check_prefix(content, conf().get("single_chat_prefix", [""]))
                if match_prefix is not None:  # 判断如果匹配到自定义前缀，则返回过滤掉前缀+空格后的内容
                    content = content.replace(match_prefix, "", 1).strip()
                elif self.channel_type == 'wechatcom_app':
                    # todo:企业微信自建应用不需要前导字符
                    pass
                elif context["origin_ctype"] == ContextType.VOICE:  # 如果源消息是私聊的语音消息，允许不匹配前缀，放宽条件
                    pass
                else:
                    return None
            content = content.strip()
            img_match_prefix = check_prefix(content, conf().get("image_create_prefix",[""]))
            if img_match_prefix:
                content = content.replace(img_match_prefix, "", 1)
                context.type = ContextType.IMAGE_CREATE
            else:
                context.type = ContextType.TEXT
            context.content = content.strip()
            if "desire_rtype" not in context and conf().get(
                    "always_reply_voice") and ReplyType.VOICE not in self.NOT_SUPPORT_REPLYTYPE:
                context["desire_rtype"] = ReplyType.VOICE
        elif context.type == ContextType.VOICE:
            if "desire_rtype" not in context and conf().get(
                    "voice_reply_voice") and ReplyType.VOICE not in self.NOT_SUPPORT_REPLYTYPE:
                context["desire_rtype"] = ReplyType.VOICE
        return context

    def _handle(self, context: Context):
        if context is None or not context.content:
            return
        logger.debug("[chat_channel] ready to handle context: {}".format(context))
        # reply的构建步骤
        reply = self._generate_reply(context)

        logger.debug("[chat_channel] ready to decorate reply: {}".format(reply))

        # reply的包装步骤
        if reply and reply.content:
            reply = self._decorate_reply(context, reply)

            # reply的发送步骤
            self._send_reply(context, reply)

    def _generate_reply(self, context: Context, reply: Reply = Reply()) -> Reply:
        # 插件优先处理
        e_context = PluginManager().emit_event(
            EventContext(
                Event.ON_HANDLE_CONTEXT,
                {"channel": self, "context": context, "reply": reply},
            )
        )
        reply = e_context["reply"]
        # 如果插件已经处理（如 reply.content 不为空），直接返回，不再走 DIFY
        if reply and reply.content:
            logger.debug("[chat_channel] plugin handled reply, skip DIFY: {}".format(reply))
            return reply

        # 群聊消息的特殊处理：如果不是插件命令，但内容中不包含群聊前缀，则不发送给DIFY
        if context.get("isgroup", False):
            # 获取原始内容（未去除前缀的内容）
            raw_content = context["msg"].content if hasattr(context["msg"], "content") else context.content
            # 检查是否是插件命令
            plugin_prefix = conf().get("plugin_trigger_prefix", "$")
            is_plugin_command = raw_content.startswith(plugin_prefix)
            # 检查是否有群聊前缀
            group_prefixes = conf().get("group_chat_prefix", ["@bot"])
            has_group_prefix = any(raw_content.startswith(prefix) for prefix in group_prefixes if prefix)
            # 检查是否被@
            is_at = hasattr(context["msg"], "is_at") and context["msg"].is_at
            # 检查是否包含关键词
            has_keyword = check_contain(raw_content, conf().get("group_chat_keyword", []))
            
            # 如果不是插件命令，而且没有群聊前缀、不是@，也没有关键词，则不发送给DIFY
            if not is_plugin_command and not has_group_prefix and not is_at and not has_keyword:
                logger.debug(f"[chat_channel] group message without valid trigger, skip DIFY: {raw_content[:30]}...")
                return Reply(ReplyType.TEXT, "")  # 返回空回复，不触发DIFY
                
        # 否则才走 DIFY
        # 原有 DIFY 处理逻辑
        if not e_context.is_pass():
            logger.debug("[chat_channel] ready to handle context: type={}, content={}".format(context.type, context.content))
            if context.type == ContextType.TEXT or context.type == ContextType.IMAGE_CREATE:  # 文字和图片消息
                context["channel"] = e_context["channel"]
                reply = super().build_reply_content(context.content, context)
            elif context.type == ContextType.VOICE:  # 语音消息
                cmsg = context["msg"]
                cmsg.prepare()
                file_path = context.content
                wav_path = os.path.splitext(file_path)[0] + ".wav"
                try:
                    any_to_wav(file_path, wav_path)
                except Exception as e:  # 转换失败，直接使用mp3，对于某些api，mp3也可以识别
                    logger.warning("[chat_channel]any to wav error, use raw path. " + str(e))
                    wav_path = file_path
                # 语音识别
                reply = super().build_voice_to_text(wav_path)
                # 删除临时文件
                try:
                    os.remove(file_path)
                    if wav_path != file_path:
                        os.remove(wav_path)
                except Exception as e:
                    pass
                    # logger.warning("[chat_channel]delete temp file error: " + str(e))

                if reply.type == ReplyType.TEXT:
                    new_context = self._compose_context(ContextType.TEXT, reply.content, **context.kwargs)
                    if new_context:
                        reply = self._generate_reply(new_context)
                    else:
                        return
            elif context.type == ContextType.IMAGE:  # 图片消息，当前仅做下载保存到本地的逻辑
                memory.USER_IMAGE_CACHE[context["session_id"]] = {
                    "path": context.content,
                    "msg": context.get("msg")
                }
            elif context.type == ContextType.ACCEPT_FRIEND:  # 好友申请，匹配字符串
                reply = self._build_friend_request_reply(context)
            elif context.type == ContextType.SHARING:  # 分享信息，当前无默认逻辑
                pass
            elif context.type == ContextType.FUNCTION or context.type == ContextType.FILE:  # 文件消息及函数调用等，当前无默认逻辑
                pass
            else:
                logger.warning("[chat_channel] unknown context type: {}".format(context.type))
                return
        return reply

    def _decorate_reply(self, context: Context, reply: Reply) -> Reply:
        if reply and reply.type:
            e_context = PluginManager().emit_event(
                EventContext(
                    Event.ON_DECORATE_REPLY,
                    {"channel": self, "context": context, "reply": reply},
                )
            )
            reply = e_context["reply"]
            desire_rtype = context.get("desire_rtype")
            if not e_context.is_pass() and reply and reply.type:
                if reply.type in self.NOT_SUPPORT_REPLYTYPE:
                    logger.error("[chat_channel]reply type not support: " + str(reply.type))
                    reply.type = ReplyType.ERROR
                    reply.content = "不支持发送的消息类型: " + str(reply.type)

                # 新增：自动处理IMAGE_URL，下载为本地图片
                if reply.type == ReplyType.IMAGE_URL:
                    local_path = download_image_to_tmp(reply.content)
                    if local_path:
                        reply.type = ReplyType.IMAGE
                        reply.content = local_path
                    else:
                        reply.type = ReplyType.ERROR
                        reply.content = "图片下载失败，无法发送。"

                if reply.type == ReplyType.TEXT:
                    reply_text = reply.content
                    if desire_rtype == ReplyType.VOICE and ReplyType.VOICE not in self.NOT_SUPPORT_REPLYTYPE:
                        reply = super().build_text_to_voice(reply.content)
                        return self._decorate_reply(context, reply)
                    if context.get("isgroup", False):
                        if not conf().get("no_need_at", False):
                            # 新增：自动查群成员接口/缓存获取@名称
                            at_name = None
                            try:
                                group_id = context["msg"].other_user_id
                                user_id = context["msg"].actual_user_id
                                at_name = get_group_member_display_name(group_id, user_id)
                            except Exception as e:
                                logger.warning(f"[chat_channel] 获取@名称失败: {e}")
                            if not at_name:
                                at_name = context["msg"].actual_user_nickname or context["msg"].from_user_nickname or "群成员"
                            reply_text = f"@{at_name}\n" + reply_text.strip()
                        reply_text = conf().get("group_chat_reply_prefix", "") + reply_text + conf().get(
                            "group_chat_reply_suffix", "")
                    else:
                        reply_text = conf().get("single_chat_reply_prefix", "") + reply_text + conf().get(
                            "single_chat_reply_suffix", "")
                    reply.content = reply_text
                elif reply.type == ReplyType.ERROR or reply.type == ReplyType.INFO:
                    reply.content = "[" + str(reply.type) + "]\n" + reply.content
                elif reply.type == ReplyType.IMAGE_URL or reply.type == ReplyType.VOICE or reply.type == ReplyType.IMAGE or reply.type == ReplyType.FILE or reply.type == ReplyType.VIDEO or reply.type == ReplyType.VIDEO_URL:
                    pass
                elif reply.type == ReplyType.ACCEPT_FRIEND:
                    pass
                else:
                    logger.error("[chat_channel] unknown reply type: {}".format(reply.type))
                    return
            if desire_rtype and desire_rtype != reply.type and reply.type not in [ReplyType.ERROR, ReplyType.INFO]:
                logger.warning("[chat_channel] desire_rtype: {}, but reply type: {}".format(context.get("desire_rtype"), reply.type))
            return reply

    def _send_reply(self, context: Context, reply: Reply):
        if reply and reply.type:
            e_context = PluginManager().emit_event(
                EventContext(
                    Event.ON_SEND_REPLY,
                    {"channel": self, "context": context, "reply": reply},
                )
            )
            reply = e_context["reply"]
            if not e_context.is_pass() and reply and reply.type:
                logger.debug("[chat_channel] ready to send reply: {}, context: {}".format(reply, context))
                self._send(reply, context)

    def _send(self, reply: Reply, context: Context, retry_cnt=0):
        try:
            self.send(reply, context)
        except Exception as e:
            logger.error("[chat_channel] sendMsg error: {}".format(str(e)))
            if isinstance(e, NotImplementedError):
                return
            logger.exception(e)
            if retry_cnt < 2:
                time.sleep(3 + 3 * retry_cnt)
                self._send(reply, context, retry_cnt + 1)

    # 处理好友申请
    def _build_friend_request_reply(self, context):
        if isinstance(context.content, dict) and "Content" in context.content:
            logger.info("friend request content: {}".format(context.content["Content"]))
            if context.content["Content"] in conf().get("accept_friend_commands", []):
                return Reply(type=ReplyType.ACCEPT_FRIEND, content=True)
            else:
                return Reply(type=ReplyType.ACCEPT_FRIEND, content=False)
        else:
            logger.error("Invalid context content: {}".format(context.content))
            return None

    def _success_callback(self, session_id, **kwargs):  # 线程正常结束时的回调函数
        logger.debug("Worker return success, session_id = {}".format(session_id))

    def _fail_callback(self, session_id, exception, **kwargs):  # 线程异常结束时的回调函数
        logger.exception("Worker return exception: {}".format(exception))

    def _thread_pool_callback(self, session_id, **kwargs):
        def func(worker: Future):
            try:
                worker_exception = worker.exception()
                if worker_exception:
                    self._fail_callback(session_id, exception=worker_exception, **kwargs)
                else:
                    self._success_callback(session_id, **kwargs)
            except CancelledError as e:
                logger.info("Worker cancelled, session_id = {}".format(session_id))
            except Exception as e:
                logger.exception("Worker raise exception: {}".format(e))
            with self.lock:
                self.sessions[session_id][1].release()

        return func

    def produce(self, context: Context):
        session_id = context.get("session_id", 0)
        with self.lock:
            if session_id not in self.sessions:
                self.sessions[session_id] = [
                    Dequeue(),
                    threading.BoundedSemaphore(conf().get("concurrency_in_session", 4)),
                ]
            if context.type == ContextType.TEXT and context.content.startswith("#"):
                self.sessions[session_id][0].putleft(context)  # 优先处理管理命令
            else:
                self.sessions[session_id][0].put(context)

    # 消费者函数，单独线程，用于从消息队列中取出消息并处理
    def consume(self):
        while True:
            with self.lock:
                session_ids = list(self.sessions.keys())
            for session_id in session_ids:
                with self.lock:
                    context_queue, semaphore = self.sessions[session_id]
                if semaphore.acquire(blocking=False):  # 等线程处理完毕才能删除
                    if not context_queue.empty():
                        context = context_queue.get()
                        logger.debug("[chat_channel] consume context: {}".format(context))
                        future: Future = handler_pool.submit(self._handle, context)
                        future.add_done_callback(self._thread_pool_callback(session_id, context=context))
                        with self.lock:
                            if session_id not in self.futures:
                                self.futures[session_id] = []
                            self.futures[session_id].append(future)
                    elif semaphore._initial_value == semaphore._value + 1:  # 除了当前，没有任务再申请到信号量，说明所有任务都处理完毕
                        with self.lock:
                            self.futures[session_id] = [t for t in self.futures[session_id] if not t.done()]
                            assert len(self.futures[session_id]) == 0, "thread pool error"
                            del self.sessions[session_id]
                    else:
                        semaphore.release()
            time.sleep(0.2)

    # 取消session_id对应的所有任务，只能取消排队的消息和已提交线程池但未执行的任务
    def cancel_session(self, session_id):
        with self.lock:
            if session_id in self.sessions:
                for future in self.futures[session_id]:
                    future.cancel()
                cnt = self.sessions[session_id][0].qsize()
                if cnt > 0:
                    logger.info("Cancel {} messages in session {}".format(cnt, session_id))
                self.sessions[session_id][0] = Dequeue()

    def cancel_all_session(self):
        with self.lock:
            for session_id in self.sessions:
                for future in self.futures[session_id]:
                    future.cancel()
                cnt = self.sessions[session_id][0].qsize()
                if cnt > 0:
                    logger.info("Cancel {} messages in session {}".format(cnt, session_id))
                self.sessions[session_id][0] = Dequeue()


def check_prefix(content, prefix_list):
    if not prefix_list:
        return None
    for prefix in prefix_list:
        if content.startswith(prefix):
            return prefix
    return None


def check_contain(content, keyword_list):
    if not keyword_list:
        return None
    for ky in keyword_list:
        if content.find(ky) != -1:
            return True
    return None
