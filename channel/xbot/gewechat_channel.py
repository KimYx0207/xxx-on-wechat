import os
import time
import json
import threading
import uuid
import base64
import requests
import tempfile
import urllib.request
from pydub import AudioSegment
from io import BytesIO
import pysilk
import subprocess
from PIL import Image
import io

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
                try:
                    import os
                    import base64
                    import subprocess
                    import tempfile
                    
                    voice_file = reply.content
                    
                    # 记录开始处理
                    logger.info(f"[xbot] 开始处理语音: {voice_file}")
                    
                    # 获取文件格式和音频类型
                    file_ext = os.path.splitext(voice_file)[1].lower().replace('.', '')
                    logger.info(f"[xbot] 语音文件格式: {file_ext}, 大小: {os.path.getsize(voice_file)}字节")
                    
                    # 对于MP3格式，先转换为AMR格式再发送
                    final_voice_file = voice_file
                    converted_file = None
                    voice_format = 0  # 默认使用AMR格式
                    
                    if file_ext == 'mp3':
                        try:
                            # 创建临时AMR文件
                            fd, amr_file = tempfile.mkstemp(suffix='.amr')
                            os.close(fd)
                            
                            # 使用ffmpeg转换为AMR
                            cmd = [
                                "ffmpeg", 
                                "-y",          # 覆盖输出
                                "-i", voice_file,  # 输入文件
                                "-ar", "8000",  # 采样率
                                "-ab", "12.2k",  # 比特率
                                "-ac", "1",    # 单声道
                                amr_file       # 输出文件
                            ]
                            
                            process = subprocess.run(
                                cmd, 
                                stdout=subprocess.PIPE, 
                                stderr=subprocess.PIPE, 
                                text=True, 
                                check=False
                            )
                            
                            if process.returncode == 0 and os.path.exists(amr_file) and os.path.getsize(amr_file) > 0:
                                logger.info(f"[xbot] MP3转换为AMR成功: {amr_file}, 大小: {os.path.getsize(amr_file)}字节")
                                final_voice_file = amr_file
                                converted_file = amr_file
                                voice_format = 0  # AMR格式
                            else:
                                logger.warning(f"[xbot] MP3转换为AMR失败: {process.stderr}")
                                # 转换失败时仍使用原始MP3
                                voice_format = 2  # MP3格式
                        except Exception as e:
                            logger.warning(f"[xbot] MP3转换为AMR异常: {e}")
                            voice_format = 2  # 失败时使用MP3格式
                    elif file_ext == 'amr':
                        voice_format = 0  # AMR格式
                    elif file_ext == 'wav':
                        voice_format = 3  # WAV格式 
                    else:
                        voice_format = 2  # 默认按MP3处理
                        
                    # 读取语音文件
                    with open(final_voice_file, "rb") as f:
                        voice_byte = f.read()
                        voice_base64 = base64.b64encode(voice_byte).decode()
                    
                    # 获取音频时长 - 尝试多种方法
                    voice_time = None
                    
                    # 方法1：尝试使用pydub获取时长
                    try:
                        audio = AudioSegment.from_file(final_voice_file)
                        voice_time = len(audio)  # 毫秒
                        logger.info(f"[xbot] 使用pydub获取音频时长: {voice_time}毫秒")
                    except Exception as e:
                        logger.warning(f"[xbot] 使用pydub获取音频时长失败: {e}")
                    
                    # 方法2：尝试使用mutagen获取时长
                    if voice_time is None:
                        try:
                            if file_ext == 'mp3':
                                from mutagen.mp3 import MP3
                                audio = MP3(final_voice_file)
                                if audio.info and hasattr(audio.info, 'length'):
                                    voice_time = int(audio.info.length * 1000)
                                    logger.info(f"[xbot] 使用mutagen获取MP3时长: {voice_time}毫秒")
                            elif file_ext == 'wav':
                                from mutagen.wave import WAVE
                                audio = WAVE(final_voice_file)
                                if audio.info and hasattr(audio.info, 'length'):
                                    voice_time = int(audio.info.length * 1000)
                                    logger.info(f"[xbot] 使用mutagen获取WAV时长: {voice_time}毫秒")
                            elif file_ext == 'amr':
                                # AMR格式没有直接支持，使用文件大小估算
                                file_size = os.path.getsize(final_voice_file)
                                # 粗略估计：AMR语音平均比特率约为8kbps
                                voice_time = int((file_size * 8) / 8000 * 1000)
                                logger.info(f"[xbot] 基于文件大小估算AMR时长: {voice_time}毫秒")
                        except Exception as e:
                            logger.warning(f"[xbot] 使用mutagen获取音频时长失败: {e}")
                    
                    # 兜底：未能获取时长则设置一个保守的值
                    if voice_time is None or voice_time < 1000:  # 至少1秒
                        voice_time = 6000  # 默认6秒
                        logger.warning(f"[xbot] 未能获取有效时长，使用默认值: {voice_time}毫秒")
                    elif voice_time > 60000:  # 不超过60秒
                        voice_time = 60000
                        logger.warning(f"[xbot] 时长超过限制，设置为最大值: {voice_time}毫秒")
                    
                    logger.info(f"[xbot] 最终语音时长: {voice_time}毫秒, 格式类型: {voice_format}")
                    
                    # 构造API参数
                    data = {
                        "Wxid": self.wxid,
                        "ToWxid": receiver,
                        "Base64": voice_base64,
                        "Type": voice_format,
                        "VoiceTime": voice_time
                    }
                    
                    # 发送请求
                    url = f"{self.base_url.rstrip('/')}/Msg/SendVoice"
                    logger.info(f"[xbot] 发送语音请求: {url}, 接收者={receiver}, 格式={voice_format}")
                    
                    resp = requests.post(url, json=data, timeout=10)
                    success = False
                    
                    if resp.status_code == 200:
                        try:
                            json_resp = resp.json()
                            if json_resp.get("Success"):
                                logger.info(f"[xbot] 语音发送成功: {receiver}")
                                success = True
                            else:
                                logger.error(f"[xbot] 语音发送失败: {json_resp}")
                                # 如果失败且不是AMR格式，尝试以AMR格式重试
                                if voice_format != 0 and file_ext != 'amr':
                                    logger.info(f"[xbot] 尝试以AMR格式重新发送语音")
                                    data["Type"] = 0
                                    resp = requests.post(url, json=data, timeout=10)
                                    if resp.status_code == 200 and resp.json().get("Success"):
                                        logger.info(f"[xbot] 以AMR格式重新发送成功: {receiver}")
                                        success = True
                                    else:
                                        logger.error(f"[xbot] 以AMR格式重新发送失败: {resp.text[:200]}")
                        except Exception as e:
                            logger.error(f"[xbot] 解析响应失败: {e}, 响应: {resp.text[:200]}")
                    else:
                        logger.error(f"[xbot] 语音发送请求失败: 状态码={resp.status_code}, 响应={resp.text[:200]}")
                    
                    # 清理临时文件
                    if converted_file and os.path.exists(converted_file):
                        try:
                            os.remove(converted_file)
                            logger.info(f"[xbot] 已清理临时转换文件: {converted_file}")
                        except Exception as e:
                            logger.warning(f"[xbot] 清理临时文件失败: {e}")
                    
                    # 如果发送失败，发送文字提示
                    if not success:
                        try:
                            error_msg = "语音发送失败，请稍后再试"
                            self.client.send_text(self.wxid, receiver, error_msg)
                        except:
                            pass
                
                except Exception as e:
                    logger.error(f"[xbot] 语音处理异常: {e}")
                    # 不再抛出异常，避免中断整个发送过程
                    try:
                        error_msg = f"语音发送失败，请稍后再试"
                        self.client.send_text(self.wxid, receiver, error_msg)
                    except:
                        pass
                
            elif reply.type == ReplyType.VIDEO_URL:
                # 视频URL消息
                try:
                    import tempfile
                    import os
                    import base64
                    
                    video_url = reply.content
                    logger.info(f"[xbot] 开始处理视频URL: {video_url}")
                    
                    if not video_url:
                        logger.error("[xbot] 视频URL为空")
                        self.client.send_text(self.wxid, receiver, "视频URL无效")
                        return
                    
                    # 下载视频
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
                    }
                    
                    # 创建临时文件保存视频
                    temp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
                            temp_path = temp_file.name
                        
                        logger.info(f"[xbot] 正在下载视频至临时文件: {temp_path}")
                        
                        # 下载视频到临时文件
                        with open(temp_path, 'wb') as f:
                            response = requests.get(video_url, headers=headers, stream=True, timeout=60)
                            response.raise_for_status()
                            total_size = int(response.headers.get('Content-Length', 0))
                            downloaded = 0
                            
                            for chunk in response.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    percent = int(downloaded / total_size * 100) if total_size > 0 else 0
                                    if percent % 20 == 0:  # 每20%记录一次
                                        logger.info(f"[xbot] 视频下载进度: {percent}%")
                        
                        content_type = response.headers.get('Content-Type', '')
                        logger.info(f"[xbot] 视频下载完成: {temp_path}, 内容类型: {content_type}, 大小: {downloaded}字节")
                        
                        # 用ffmpeg提取第一帧为缩略图
                        thumb_path = temp_path + "_thumb.jpg"
                        try:
                            import subprocess
                            cmd = [
                                "ffmpeg", "-y", "-i", temp_path,
                                "-vf", "select=eq(n\\,0)", "-q:v", "2", "-frames:v", "1", thumb_path
                            ]
                            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                        except Exception as e:
                            logger.warning(f"[xbot] ffmpeg提取缩略图失败: {e}")
                            thumb_path = None
                        
                        # 用ffprobe获取视频时长
                        video_length = 10
                        try:
                            cmd = [
                                "ffprobe", "-v", "error", "-show_entries",
                                "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", temp_path
                            ]
                            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                            duration = float(result.stdout.decode().strip())
                            video_length = int(duration)
                        except Exception as e:
                            logger.warning(f"[xbot] ffprobe获取视频时长失败: {e}")
                        
                        # 读取视频和缩略图为base64
                        with open(temp_path, 'rb') as f:
                            video_base64 = base64.b64encode(f.read()).decode('utf-8')
                        if thumb_path and os.path.exists(thumb_path):
                            with open(thumb_path, 'rb') as f:
                                thumb_data = base64.b64encode(f.read()).decode('utf-8')
                        else:
                            thumb_data = ""
                        
                        logger.info(f"[xbot] 视频Base64大小: {len(video_base64)}, 时长: {video_length}秒")
                        
                        # 构造请求数据
                        data = {
                            "Wxid": self.wxid,
                            "ToWxid": receiver,
                            "Base64": "data:video/mp4;base64," + video_base64,
                            "ImageBase64": "data:image/jpeg;base64," + thumb_data,
                            "PlayLength": video_length
                        }
                        
                        # 发送请求
                        url = f"{self.base_url.rstrip('/')}/Msg/SendVideo"
                        logger.info(f"[xbot] 发送视频请求: {url}, 数据大小: {len(video_base64)//1024}KB")
                        
                        resp = requests.post(url, json=data, timeout=60)  # 增大超时时间
                        
                        # 清理临时文件
                        if temp_path and os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                                logger.debug(f"[xbot] 已清理临时视频文件: {temp_path}")
                            except Exception as e:
                                logger.warning(f"[xbot] 清理临时文件失败: {e}")
                        
                        if resp.status_code == 200:
                            try:
                                resp_data = resp.json()
                                if resp_data.get("Success"):
                                    logger.info(f"[xbot] 发送视频成功: {receiver}")
                                else:
                                    logger.error(f"[xbot] 发送视频失败: {resp_data}")
                                    # 发送错误消息
                                    self.client.send_text(self.wxid, receiver, "视频发送失败，请稍后再试")
                            except Exception as e:
                                logger.error(f"[xbot] 视频响应解析错误: {e}")
                                self.client.send_text(self.wxid, receiver, "视频发送过程中出现错误")
                        else:
                            logger.error(f"[xbot] 视频API请求失败: {resp.status_code}, {resp.text}")
                            self.client.send_text(self.wxid, receiver, "视频发送请求失败，请稍后再试")
                            
                    except Exception as download_err:
                        logger.error(f"[xbot] 视频下载失败: {download_err}")
                        self.client.send_text(self.wxid, receiver, "视频下载失败，请稍后再试")
                        # 清理临时文件
                        if temp_path and os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                            except:
                                pass
                except Exception as e:
                    logger.error(f"[xbot] 处理视频URL异常: {e}")
                    self.client.send_text(self.wxid, receiver, "处理视频时出错，请稍后再试")
                
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
