import os
import time
import json
import threading
import uuid
import base64
import requests

from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel
from channel.xbot.gewechat_message import XBotMessage
from common.log import logger
from common.singleton import singleton
from common.tmp_dir import TmpDir
from config import conf, save_config
from lib.xbot.client import XBotClient
from voice.audio_convert import mp3_to_silk

MAX_UTF8_LEN = 2048
ROBOT_STAT_PATH = os.path.join(os.path.dirname(__file__), '../../resource/robot_stat.json')
ROBOT_STAT_PATH = os.path.abspath(ROBOT_STAT_PATH)

@singleton
class XBotChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self):
        super().__init__()
        self.base_url = conf().get("xbot_base_url")
        self.client = XBotClient(self.base_url)
        self.robot_stat = None
        self.wxid = None
        self.device_id = None
        self.device_name = None
        logger.info(f"[xbot] init: base_url: {self.base_url}")

    def startup(self):
        self._ensure_login()
        logger.info(f"[xbot] channel startup, wxid: {self.wxid}")
        threading.Thread(target=self._sync_message_loop, daemon=True).start()

    def _ensure_login(self):
        stat = XBotClient.load_robot_stat(ROBOT_STAT_PATH)
        if stat and stat.get("wxid") and stat.get("device_id") and stat.get("device_name"):
            self.wxid = stat["wxid"]
            self.device_id = stat["device_id"]
            self.device_name = stat["device_name"]
            logger.info(f"[xbot] 已加载本地登录信息: wxid={self.wxid}, device_id={self.device_id}, device_name={self.device_name}")
            # 1. 先尝试二次登录
            try:
                twice_result = self.client.twice_login(self.wxid)
                if twice_result.get("Success"):
                    logger.info(f"[xbot] 二次登录成功: wxid={self.wxid}")
                    return
                else:
                    logger.warning(f"[xbot] 二次登录失败: {twice_result}")
            except Exception as e:
                logger.warning(f"[xbot] 二次登录异常: {e}")
            # 2. 再尝试唤醒登录
            try:
                awaken_result = self.client.awaken_login(self.wxid)
                if awaken_result.get("Success"):
                    logger.info(f"[xbot] 唤醒登录成功: wxid={self.wxid}")
                    return
                else:
                    logger.warning(f"[xbot] 唤醒登录失败: {awaken_result}")
            except Exception as e:
                logger.warning(f"[xbot] 唤醒登录异常: {e}")
            logger.info("[xbot] 本地登录信息失效，进入扫码登录流程")
        # 3. 全部失败才进入二维码扫码登录
        self.device_id = str(uuid.uuid4())
        self.device_name = f"XBot_{self.device_id[:8]}"
        qr_resp = self.client.get_qr(self.device_id, self.device_name)
        uuid_code = qr_resp.get("Data", {}).get("Uuid")
        qr_url = qr_resp.get("Data", {}).get("QrUrl")
        if not qr_url or not uuid_code:
            logger.error(f"[xbot] 未获取到二维码链接或uuid，返回内容: {qr_resp}")
            raise Exception("未获取到二维码链接或uuid，请检查后端API返回结构")
        logger.info(f"[xbot] 请扫码登录: {qr_url} (uuid: {uuid_code})")
        # 轮询检查扫码，每秒一次
        for _ in range(240):
            check = self.client.check_qr(uuid_code)
            if check.get("Data", {}).get("Status") == 1:
                wxid = check.get("Data", {}).get("Wxid")
                self.wxid = wxid
                self.robot_stat = {"wxid": wxid, "device_id": self.device_id, "device_name": self.device_name}
                XBotClient.save_robot_stat(ROBOT_STAT_PATH, self.robot_stat)
                logger.info(f"[xbot] 登录成功: wxid={wxid}")
                return
            time.sleep(1)
        raise Exception("扫码超时，请重启程序重试")

    def _sync_message_loop(self):
        while True:
            try:
                resp = self.client.sync_message(self.wxid, self.device_id, self.device_name)
                if isinstance(resp, dict) and resp.get("Data"):
                    data = resp["Data"]
                    msgs = []
                    for key in ["AddMsgs", "MsgList", "List", "Messages"]:
                        if key in data and isinstance(data[key], list):
                            msgs = data[key]
                            break
                    if msgs:
                        for msg in msgs:
                            self._handle_message(msg)
                    # 没有消息时直接跳过
                else:
                    logger.error(f"[xbot] 消息同步返回异常: {resp}")
            except Exception as e:
                logger.error(f"[xbot] 消息同步异常: {e}")
            time.sleep(0.05)

    def _handle_message(self, msg):
        xmsg = XBotMessage(msg)
        # 新增：过滤非用户消息（如 weixin、公众号、系统号等）
        if xmsg._is_non_user_message(xmsg.msg_source, xmsg.from_user_id):
            logger.info(f"[xbot] ignore non-user/system message from {xmsg.from_user_id}")
            return
        # 标准gewechat/gewe分发：封装为Context并produce
        context = self._compose_context(ContextType.TEXT, xmsg.content, msg=xmsg, isgroup=xmsg.is_group)
        if context is not None:
            self.produce(context)

    def send(self, reply: Reply, context: Context):
        """发送消息到微信
        
        Args:
            reply: 回复对象
            context: 上下文对象
        """
        # 获取接收者，优先从context的receiver获取，其次从msg中获取
        receiver = context.get("receiver")
        if not receiver and context.get("msg"):
            msg = context.get("msg")
            # 如果是群聊，接收者应该是群ID
            if hasattr(msg, "from_user_id") and "@chatroom" in (msg.from_user_id or ""):
                receiver = msg.from_user_id
            # 如果是私聊，接收者应该是发送者ID
            elif hasattr(msg, "from_user_id"):
                receiver = msg.from_user_id
            # 备用：尝试从other_user_id获取
            elif hasattr(msg, "other_user_id"):
                receiver = msg.other_user_id
                
        if not receiver:
            logger.error(f"[xbot] Cannot determine receiver for reply: {reply.type}")
            return
            
        logger.debug(f"[xbot] Sending {reply.type} to {receiver}")
        
        try:
            if reply.type in [ReplyType.TEXT, ReplyType.ERROR, ReplyType.INFO]:
                # 文本消息
                self.client.send_text(self.wxid, receiver, reply.content)
                logger.info(f"[xbot] send text to {receiver}: {reply.content[:50]}...")
                
            elif reply.type == ReplyType.IMAGE:
                # 获取 api_base_url 和 bot_wxid
                api_base_url = conf().get("xbot_base_url") or conf().get("gewechat_base_url")
                # 优先从 resource/robot_stat.json 读取 bot_wxid
                bot_wxid = None
                try:
                    resource_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../resource/robot_stat.json'))
                    with open(resource_path, "r", encoding="utf-8") as f:
                        stat = json.load(f)
                        bot_wxid = stat.get("wxid")
                except Exception as e:
                    logger.error(f"[send_image] 读取robot_stat.json失败: {e}")
                to_wxid = context.get("receiver") or context.get("group_name")
                if not to_wxid or not bot_wxid or not api_base_url:
                    logger.error(f"[send_image] 缺少参数: to_wxid={to_wxid}, bot_wxid={bot_wxid}, api_base_url={api_base_url}")
                    return
                self.send_image(reply.content, to_wxid, bot_wxid, api_base_url)
                return
                
            elif reply.type == ReplyType.VOICE:
                # 语音消息
                # 自动检测语音时长，默认为5秒
                voice_time = 5000
                voice_type = 0  # AMR 格式
                self.client.send_voice(self.wxid, receiver, reply.content, voice_type, voice_time)
                logger.info(f"[xbot] send voice to {receiver}")
                
            elif reply.type == ReplyType.VIDEO:
                # 视频消息
                if isinstance(reply.content, tuple) and len(reply.content) == 3:
                    video_data, thumb_data, play_length = reply.content
                    self.client.send_video(self.wxid, receiver, video_data, thumb_data, play_length)
                    logger.info(f"[xbot] send video to {receiver}")
                else:
                    logger.error(f"[xbot] Invalid video content format: {type(reply.content)}")
                
            elif reply.type == ReplyType.EMOJI:
                # 表情消息
                if isinstance(reply.content, tuple) and len(reply.content) == 2:
                    md5, total_len = reply.content
                    self.client.send_emoji(self.wxid, receiver, md5, total_len)
                    logger.info(f"[xbot] send emoji to {receiver}")
                else:
                    logger.error(f"[xbot] Invalid emoji content format: {type(reply.content)}")
                
            elif reply.type == ReplyType.CARD:
                # 名片消息
                if isinstance(reply.content, tuple) and len(reply.content) >= 2:
                    if len(reply.content) == 2:
                        card_wxid, card_nickname = reply.content
                        card_alias = ""
                    else:
                        card_wxid, card_nickname, card_alias = reply.content
                    self.client.share_card(self.wxid, receiver, card_wxid, card_nickname, card_alias)
                    logger.info(f"[xbot] send card to {receiver}")
                else:
                    logger.error(f"[xbot] Invalid card content format: {type(reply.content)}")
                
            elif reply.type == ReplyType.LINK:
                # 链接消息
                if isinstance(reply.content, str):
                    # 如果是XML字符串，直接发送
                    self.client.send_app_message(self.wxid, receiver, reply.content)
                    logger.info(f"[xbot] send link to {receiver}")
                elif isinstance(reply.content, tuple) and len(reply.content) >= 3:
                    # 如果是元组，构造XML
                    title, description, url, thumb_url = reply.content
                    xml = f"""<appmsg appid="" sdkver="0">
                    <title>{title}</title>
                    <des>{description}</des>
                    <url>{url}</url>
                    <thumburl>{thumb_url}</thumburl>
                    <type>5</type>
                    </appmsg>"""
                    self.client.send_app_message(self.wxid, receiver, xml)
                    logger.info(f"[xbot] send link to {receiver}")
                else:
                    logger.error(f"[xbot] Invalid link content format: {type(reply.content)}")
                
            elif reply.type == ReplyType.REVOKE:
                # 撤回消息
                if isinstance(reply.content, tuple) and len(reply.content) == 3:
                    client_msg_id, create_time, new_msg_id = reply.content
                    self.client.revoke_msg(self.wxid, receiver, client_msg_id, create_time, new_msg_id)
                    logger.info(f"[xbot] revoke msg from {receiver}")
                else:
                    logger.error(f"[xbot] Invalid revoke content format: {type(reply.content)}")
                
            else:
                logger.warning(f"[xbot] Unsupported reply type: {reply.type}")
                
        except Exception as e:
            logger.error(f"[xbot] Failed to send {reply.type} to {receiver}: {e}")
            # 尝试发送错误消息
            try:
                error_msg = f"消息发送失败: {e}"
                self.client.send_text(self.wxid, receiver, error_msg)
            except Exception as e2:
                logger.error(f"[xbot] Failed to send error message: {e2}")

    def send_image(self, image_path, to_wxid, bot_wxid, api_base_url):
        try:
            with open(image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")
            payload = {
                "Base64": img_b64,
                "ToWxid": to_wxid,
                "Wxid": bot_wxid
            }
            url = api_base_url.rstrip("/") + "/Msg/UploadImg"
            resp = requests.post(url, json=payload, timeout=10)
            logger.info(f"[send_image] POST {url} resp={resp.status_code} {resp.text[:200]}")
            if resp.status_code == 200:
                return True
            else:
                logger.error(f"[send_image] 发送图片失败: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"[send_image] 发送图片异常: {e}")
            return False
