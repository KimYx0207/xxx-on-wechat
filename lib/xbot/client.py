import requests
import os
import json

class XBotClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/')

    def _post(self, path, data=None, params=None):
        url = self.base_url + path
        headers = {'Content-Type': 'application/json'}
        try:
            resp = requests.post(url, json=data, params=params, headers=headers, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("Success", True):
                raise Exception(f"API失败: {result.get('Message', result)}")
            return result
        except Exception as e:
            raise Exception(f"请求 {url} 失败: {e}")

    # 登录相关
    def get_qr(self, device_id, device_name):
        """获取登录二维码
        
        Args:
            device_id: 设备ID，随机生成的UUID
            device_name: 设备名称，如 XBot_xxx
            
        Returns:
            返回登录二维码信息，包含 QrUrl 和 Uuid
        """
        url = self.base_url + '/Login/LoginGetQR'
        data = {"DeviceId": device_id, "DeviceName": device_name}
        try:
            resp = requests.post(url, json=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("Success", True):
                raise Exception(f"获取二维码失败: {result.get('Message', result)}")
            # 日志输出完整返回内容
            print(f"[xbot] get_qr 返回: {result}")
            # 优先取QrUrl字段
            qr_url = None
            if result.get("Data"):
                qr_url = result["Data"].get("QrUrl") or result["Data"].get("QrcodeUrl") or result["Data"].get("Url")
            result["QrUrl"] = qr_url
            return result
        except Exception as e:
            raise Exception(f"获取二维码接口失败: {e}")

    def check_qr(self, uuid):
        """检查扫码状态
        
        Args:
            uuid: 获取二维码时返回的UUID
            
        Returns:
            返回扫码状态，Status=1表示已扫码并确认
        """
        url = self.base_url + '/Login/LoginCheckQR'
        params = {"uuid": uuid}
        try:
            resp = requests.post(url, params=params, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("Success", True):
                raise Exception(f"检查扫码失败: {result.get('Message', result)}")
            return result
        except Exception as e:
            raise Exception(f"检查扫码接口失败: {e}")

    def awaken_login(self, wxid):
        """唤醒登录
        
        Args:
            wxid: 微信ID
            
        Returns:
            成功返回 {"Success": true}
        """
        url = self.base_url + '/Login/LoginAwaken'
        data = {"Wxid": wxid}
        try:
            resp = requests.post(url, json=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("Success", True):
                raise Exception(f"唤醒登录失败: {result.get('Message', result)}")
            return result
        except Exception as e:
            raise Exception(f"唤醒登录接口失败: {e}")

    def twice_login(self, wxid):
        """二次登录
        
        Args:
            wxid: 微信ID
            
        Returns:
            成功返回 {"Success": true}
        """
        url = self.base_url + '/Login/LoginTwiceAutoAuth'
        params = {"wxid": wxid}
        try:
            resp = requests.post(url, params=params, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("Success", True):
                raise Exception(f"二次登录失败: {result.get('Message', result)}")
            return result
        except Exception as e:
            raise Exception(f"二次登录接口失败: {e}")

    def heart_beat(self, wxid):
        """发送心跳包
        
        Args:
            wxid: 微信ID
            
        Returns:
            成功返回 {"Success": true}
        """
        url = self.base_url + '/Login/HeartBeat'
        params = {"wxid": wxid}
        try:
            resp = requests.post(url, params=params, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("Success", True):
                raise Exception(f"心跳失败: {result.get('Message', result)}")
            return result
        except Exception as e:
            raise Exception(f"心跳接口失败: {e}")

    def logout(self, wxid):
        """退出登录
        
        Args:
            wxid: 微信ID
            
        Returns:
            成功返回 {"Success": true}
        """
        return self._post('/Login/LogOut', params={'wxid': wxid})

    # 消息相关
    def send_text(self, wxid, to_wxid, content, at=None):
        """发送文本消息
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            content: 消息内容
            at: 群聊中需要@的微信ID，多个用逗号分隔
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid, 
            "Content": content, 
            "Type": 1
        }
        if at:
            data["At"] = at if isinstance(at, str) else ",".join(at)
        return self._post('/Msg/SendTxt', data=data)

    def send_image(self, wxid, to_wxid, base64_img):
        """发送图片消息
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            base64_img: 图片的base64编码
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid, 
            "Base64": base64_img
        }
        return self._post('/Msg/UploadImg', data=data)

    def send_voice(self, wxid, to_wxid, base64_voice, voice_type=0, voice_time=1000):
        """发送语音消息
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            base64_voice: 语音的base64编码
            voice_type: 语音类型（AMR=0, MP3=2, SILK=4, SPEEX=1, WAVE=3）
            voice_time: 语音时长，单位为毫秒，1000为1秒
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid, 
            "Base64": base64_voice, 
            "Type": voice_type, 
            "VoiceTime": voice_time
        }
        return self._post('/Msg/SendVoice', data=data)

    def send_emoji(self, wxid, to_wxid, md5, total_len):
        """发送表情消息
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            md5: 表情MD5
            total_len: 表情总长度
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid, 
            "Md5": md5, 
            "TotalLen": total_len
        }
        return self._post('/Msg/SendEmoji', data=data)

    def revoke_msg(self, wxid, to_user_name, client_msg_id, create_time, new_msg_id):
        """撤回消息
        
        Args:
            wxid: 微信ID
            to_user_name: 接收者微信ID
            client_msg_id: 客户端消息ID
            create_time: 消息创建时间
            new_msg_id: 新消息ID
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToUserName": to_user_name, 
            "ClientMsgId": client_msg_id, 
            "CreateTime": create_time, 
            "NewMsgId": new_msg_id
        }
        return self._post('/Msg/Revoke', data=data)

    def share_card(self, wxid, to_wxid, card_wxid, card_nickname, card_alias=""):
        """分享名片
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            card_wxid: 名片用户的微信ID
            card_nickname: 名片用户的昵称
            card_alias: 名片用户的别名（可选）
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "CardWxId": card_wxid,
            "CardNickName": card_nickname,
            "CardAlias": card_alias
        }
        return self._post('/Msg/ShareCard', data=data)

    def send_app_message(self, wxid, to_wxid, xml, msg_type=5):
        """发送APP消息（如小程序、链接等）
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            xml: 消息XML内容
            msg_type: 消息类型，默认5
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "Xml": xml,
            "Type": msg_type
        }
        return self._post('/Msg/SendApp', data=data)

    def send_video(self, wxid, to_wxid, base64_video, base64_thumb, play_length):
        """发送视频消息
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            base64_video: 视频的base64编码
            base64_thumb: 视频缩略图的base64编码
            play_length: 视频时长，单位为秒
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "Base64": base64_video,
            "ImageBase64": base64_thumb,
            "PlayLength": play_length
        }
        return self._post('/Msg/SendVideo', data=data)

    # 联系人相关
    def get_contacts(self, wxid):
        """获取联系人列表
        
        Args:
            wxid: 微信ID
            
        Returns:
            联系人列表
        """
        data = {
            "Wxid": wxid,
            "CurrentWxcontactSeq": 0,
            "CurrentChatRoomContactSeq": 0
        }
        return self._post('/Friend/GetContractList', data=data)

    def get_contact_detail(self, wxid, towxids):
        """获取联系人详情
        
        Args:
            wxid: 微信ID
            towxids: 联系人微信ID，多个用逗号分隔
            
        Returns:
            联系人详情
        """
        data = {
            "Wxid": wxid, 
            "Towxids": towxids,
            "ChatRoom": ""
        }
        return self._post('/Friend/GetContractDetail', data=data)

    def add_friend(self, wxid, v1, v2, verify_content=""):
        """添加好友
        
        Args:
            wxid: 微信ID
            v1: 验证参数1
            v2: 验证参数2
            verify_content: 验证内容
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "V1": v1, 
            "V2": v2,
            "VerifyContent": verify_content,
            "Opcode": 2,
            "Scene": 2
        }
        return self._post('/Friend/SendRequest', data=data)

    def delete_friend(self, wxid, to_wxid):
        """删除好友
        
        Args:
            wxid: 微信ID
            to_wxid: 好友微信ID
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid
        }
        return self._post('/Friend/Delete', data=data)

    def set_remark(self, wxid, to_wxid, remarks):
        """设置好友备注
        
        Args:
            wxid: 微信ID
            to_wxid: 好友微信ID
            remarks: 备注名
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid, 
            "Remarks": remarks
        }
        return self._post('/Friend/SetRemarks', data=data)

    # 群组相关
    def create_group(self, wxid, to_wxids):
        """创建群聊
        
        Args:
            wxid: 微信ID
            to_wxids: 成员微信ID，多个用逗号分隔
            
        Returns:
            成功返回群ID
        """
        data = {
            "Wxid": wxid, 
            "ToWxids": to_wxids
        }
        return self._post('/Group/CreateChatRoom', data=data)

    def add_group_member(self, wxid, chat_room_name, to_wxids):
        """添加群成员
        
        Args:
            wxid: 微信ID
            chat_room_name: 群ID
            to_wxids: 成员微信ID，多个用逗号分隔
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ChatRoomName": chat_room_name, 
            "ToWxids": to_wxids
        }
        return self._post('/Group/AddChatRoomMember', data=data)

    def remove_group_member(self, wxid, chat_room_name, to_wxids):
        """删除群成员
        
        Args:
            wxid: 微信ID
            chat_room_name: 群ID
            to_wxids: 成员微信ID，多个用逗号分隔
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ChatRoomName": chat_room_name, 
            "ToWxids": to_wxids
        }
        return self._post('/Group/DelChatRoomMember', data=data)

    def get_group_info(self, wxid, qid):
        """获取群信息
        
        Args:
            wxid: 微信ID
            qid: 群ID
            
        Returns:
            群信息
        """
        data = {
            "Wxid": wxid, 
            "QID": qid
        }
        return self._post('/Group/GetChatRoomInfo', data=data)

    def get_group_members(self, wxid, qid):
        """获取群成员
        
        Args:
            wxid: 微信ID
            qid: 群ID
            
        Returns:
            群成员信息
        """
        data = {
            "Wxid": wxid, 
            "QID": qid
        }
        return self._post('/Group/GetChatRoomMemberDetail', data=data)

    def set_group_announcement(self, wxid, qid, content):
        """设置群公告
        
        Args:
            wxid: 微信ID
            qid: 群ID
            content: 公告内容
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "QID": qid, 
            "Content": content
        }
        return self._post('/Group/SetChatRoomAnnouncement', data=data)

    def set_group_name(self, wxid, qid, content):
        """设置群名称
        
        Args:
            wxid: 微信ID
            qid: 群ID
            content: 群名称
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "QID": qid, 
            "Content": content
        }
        return self._post('/Group/SetChatRoomName', data=data)

    def quit_group(self, wxid, qid):
        """退出群聊
        
        Args:
            wxid: 微信ID
            qid: 群ID
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "QID": qid
        }
        return self._post('/Group/Quit', data=data)

    # 消息同步
    def sync_message(self, wxid, device_id="", device_name="", scene=0, synckey=""):
        """同步消息
        
        Args:
            wxid: 微信ID
            device_id: 设备ID（可选）
            device_name: 设备名称（可选）
            scene: 场景值，默认0
            synckey: 同步key，默认空
            
        Returns:
            消息列表
        """
        url = self.base_url + '/Msg/Sync'
        data = {
            "Wxid": wxid,
            "Scene": scene,
            "Synckey": synckey
        }
        if device_id:
            data["DeviceId"] = device_id
        if device_name:
            data["DeviceName"] = device_name
        try:
            resp = requests.post(url, json=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if not isinstance(result, dict):
                raise Exception(f"/Msg/Sync返回非dict: {result}")
            if not result.get("Success", True):
                raise Exception(f"同步消息失败: {result.get('Message', result)}")
            return result
        except Exception as e:
            raise Exception(f"同步消息请求失败: {e}")

    # 工具方法
    @staticmethod
    def load_robot_stat(path):
        """加载机器人状态
        
        Args:
            path: 状态文件路径
            
        Returns:
            状态信息字典
        """
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    @staticmethod
    def save_robot_stat(path, stat):
        """保存机器人状态
        
        Args:
            path: 状态文件路径
            stat: 状态信息字典
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(stat, f, ensure_ascii=False, indent=2)
