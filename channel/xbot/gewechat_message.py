import base64
import uuid
import re
from bridge.context import ContextType
from channel.chat_message import ChatMessage
from common.log import logger
from common.tmp_dir import TmpDir
from config import conf
# from lib.gewechat import GewechatClient  # 已废弃
import requests
import xml.etree.ElementTree as ET

# 私聊信息示例
"""
{
    "TypeName": "AddMsg",
    "Appid": "wx_xxx",
    "Data": {
        "MsgId": 177581074,
        "FromUserName": {
            "string": "wxid_fromuser"
        },
        "ToUserName": {
            "string": "wxid_touser"
        },
        "MsgType": 49,
        "Content": {
            "string": ""
        },
        "Status": 3,
        "ImgStatus": 1,
        "ImgBuf": {
            "iLen": 0
        },
        "CreateTime": 1733410112,
        "MsgSource": "<msgsource>xx</msgsource>\n",
        "PushContent": "xxx",
        "NewMsgId": 5894648508580188926,
        "MsgSeq": 773900156
    },
    "Wxid": "wxid_gewechat_bot"  // 使用gewechat登录的机器人wxid
}
"""

# 群聊信息示例
"""
{
    "TypeName": "AddMsg",
    "Appid": "wx_xxx",
    "Data": {
        "MsgId": 585326344,
        "FromUserName": {
            "string": "xxx@chatroom"
        },
        "ToUserName": {
            "string": "wxid_gewechat_bot" // 接收到此消息的wxid, 即使用gewechat登录的机器人wxid
        },
        "MsgType": 1,
        "Content": {
            "string": "wxid_xxx:\n@name msg_content" // 发送消息人的wxid和消息内容(包含@name)
        },
        "Status": 3,
        "ImgStatus": 1,
        "ImgBuf": {
            "iLen": 0
        },
        "CreateTime": 1733447040,
        "MsgSource": "<msgsource>\n\t<atuserlist><![CDATA[,wxid_wvp31dkffyml19]]></atuserlist>\n\t<pua>1</pua>\n\t<silence>0</silence>\n\t<membercount>3</membercount>\n\t<signature>V1_cqxXBat9|v1_cqxXBat9</signature>\n\t<tmp_node>\n\t\t<publisher-id></publisher-id>\n\t</tmp_node>\n</msgsource>\n",
        "PushContent": "xxx在群聊中@了你",
        "NewMsgId": 8449132831264840264,
        "MsgSeq": 773900177
    },
    "Wxid": "wxid_gewechat_bot"  // 使用gewechat登录的机器人wxid
}
"""

# 群邀请消息示例
"""
{
    "TypeName": "AddMsg",
    "Appid": "wx_xxx",
    "Data": {
        "MsgId": 488566999,
        "FromUserName": {
            "string": "xxx@chatroom"
        },
        "ToUserName": {
            "string": "wxid_gewechat_bot"
        },
        "MsgType": 10002,
        "Content": {
            "string": "53760920521@chatroom:\n<sysmsg type=\"sysmsgtemplate\">\n\t<sysmsgtemplate>\n\t\t<content_template type=\"tmpl_type_profile\">\n\t\t\t<plain><![CDATA[]]></plain>\n\t\t\t<template><![CDATA[\"$username$\"邀请\"$names$\"加入了群聊]]></template>\n\t\t\t<link_list>\n\t\t\t\t<link name=\"username\" type=\"link_profile\">\n\t\t\t\t\t<memberlist>\n\t\t\t\t\t\t<member>\n\t\t\t\t\t\t\t<username><![CDATA[wxid_eaclcf34ny6221]]></username>\n\t\t\t\t\t\t\t<nickname><![CDATA[刘贺]]></nickname>\n\t\t\t\t\t\t</member>\n\t\t\t\t\t</memberlist>\n\t\t\t\t</link>\n\t\t\t\t<link name=\"names\" type=\"link_profile\">\n\t\t\t\t\t<memberlist>\n\t\t\t\t\t\t<member>\n\t\t\t\t\t\t\t<username><![CDATA[wxid_mmwc3zzkfcl922]]></username>\n\t\t\t\t\t\t\t<nickname><![CDATA[郑德娟]]></nickname>\n\t\t\t\t\t\t</member>\n\t\t\t\t\t</memberlist>\n\t\t\t\t\t<separator><![CDATA[、]]></separator>\n\t\t\t\t</link>\n\t\t\t</link_list>\n\t\t</content_template>\n\t</sysmsgtemplate>\n</sysmsg>\n"
        },
        "Status": 4,
        "ImgStatus": 1,
        "ImgBuf": {
            "iLen": 0
        },
        "CreateTime": 1736820013,
        "MsgSource": "<msgsource>\n\t<tmp_node>\n\t\t<publisher-id></publisher-id>\n\t</tmp_node>\n</msgsource>\n",
        "NewMsgId": 5407479395895269893,
        "MsgSeq": 821038175
    },
    "Wxid": "wxid_gewechat_bot"
}
"""

"""
{
    "TypeName": "ModContacts",
    "Appid": "wx_xxx",
    "Data": {
        "UserName": {
            "string": "xxx@chatroom"
        },
        "NickName": {
            "string": "测试2"
        },
        "PyInitial": {
            "string": "CS2"
        },
        "QuanPin": {
            "string": "ceshi2"
        },
        "Sex": 0,
        "ImgBuf": {
            "iLen": 0
        },
        "BitMask": 4294967295,
        "BitVal": 2,
        "ImgFlag": 1,
        "Remark": {},
        "RemarkPyinitial": {},
        "RemarkQuanPin": {},
        "ContactType": 0,
        "RoomInfoCount": 0,
        "DomainList": [
            {}
        ],
        "ChatRoomNotify": 1,
        "AddContactScene": 0,
        "PersonalCard": 0,
        "HasWeiXinHdHeadImg": 0,
        "VerifyFlag": 0,
        "Level": 0,
        "Source": 0,
        "ChatRoomOwner": "wxid_xxx",
        "WeiboFlag": 0,
        "AlbumStyle": 0,
        "AlbumFlag": 0,
        "SnsUserInfo": {
            "SnsFlag": 0,
            "SnsBgobjectId": 0,
            "SnsFlagEx": 0
        },
        "CustomizedInfo": {
            "BrandFlag": 0
        },
        "AdditionalContactList": {
            "LinkedinContactItem": {}
        },
        "ChatroomMaxCount": 10008,
        "DeleteFlag": 0,
        "Description": "\b\u0004\u0012\u001c\n\u0013wxid_xxx0\u0001@\u0000\u0001\u0000\u0012\u001c\n\u0013wxid_xxx0\u0001@\u0000\u0001\u0000\u0012\u001c\n\u0013wxid_xxx0\u0001@\u0000\u0001\u0000\u0012\u001c\n\u0013wxid_xxx0\u0001@\u0000\u0001\u0000\u0018\u0001\"\u0000(\u00008\u0000",
        "ChatroomStatus": 5,
        "Extflag": 0,
        "ChatRoomBusinessType": 0
    },
    "Wxid": "wxid_xxx"
}
"""

# 群聊中移除用户示例
"""
{
    "UserName": {
        "string": "xxx@chatroom"
    },
    "NickName": {
        "string": "AITestGroup"
    },
    "PyInitial": {
        "string": "AITESTGROUP"
    },
    "QuanPin": {
        "string": "AITestGroup"
    },
    "Sex": 0,
    "ImgBuf": {
        "iLen": 0
    },
    "BitMask": 4294967295,
    "BitVal": 2,
    "ImgFlag": 1,
    "Remark": {},
    "RemarkPyinitial": {},
    "RemarkQuanPin": {},
    "ContactType": 0,
    "RoomInfoCount": 0,
    "DomainList": [
        {}
    ],
    "ChatRoomNotify": 1,
    "AddContactScene": 0,
    "PersonalCard": 0,
    "HasWeiXinHdHeadImg": 0,
    "VerifyFlag": 0,
    "Level": 0,
    "Source": 0,
    "ChatRoomOwner": "wxid_xxx",
    "WeiboFlag": 0,
    "AlbumStyle": 0,
    "AlbumFlag": 0,
    "SnsUserInfo": {
        "SnsFlag": 0,
        "SnsBgobjectId": 0,
        "SnsFlagEx": 0
    },
    "CustomizedInfo": {
        "BrandFlag": 0
    },
    "AdditionalContactList": {
        "LinkedinContactItem": {}
    },
    "ChatroomMaxCount": 10037,
    "DeleteFlag": 0,
    "Description": "\b\u0002\u0012\u001c\n\u0013wxid_eacxxxx\u0001@\u0000�\u0001\u0000\u0012\u001c\n\u0013wxid_xxx\u0001@\u0000�\u0001\u0000\u0018\u0001\"\u0000(\u00008\u0000",
    "ChatroomStatus": 4,
    "Extflag": 0,
    "ChatRoomBusinessType": 0
}
"""

# 群聊中移除用户示例
"""
{
    "TypeName": "ModContacts",
    "Appid": "wx_xxx",
    "Data": {
        "UserName": {
            "string": "xxx@chatroom"
        },
        "NickName": {
            "string": "测试2"
        },
        "PyInitial": {
            "string": "CS2"
        },
        "QuanPin": {
            "string": "ceshi2"
        },
        "Sex": 0,
        "ImgBuf": {
            "iLen": 0
        },
        "BitMask": 4294967295,
        "BitVal": 2,
        "ImgFlag": 2,
        "Remark": {},
        "RemarkPyinitial": {},
        "RemarkQuanPin": {},
        "ContactType": 0,
        "RoomInfoCount": 0,
        "DomainList": [
            {}
        ],
        "ChatRoomNotify": 1,
        "AddContactScene": 0,
        "PersonalCard": 0,
        "HasWeiXinHdHeadImg": 0,
        "VerifyFlag": 0,
        "Level": 0,
        "Source": 0,
        "ChatRoomOwner": "wxid_xxx",
        "WeiboFlag": 0,
        "AlbumStyle": 0,
        "AlbumFlag": 0,
        "SnsUserInfo": {
            "SnsFlag": 0,
            "SnsBgobjectId": 0,
            "SnsFlagEx": 0
        },
        "SmallHeadImgUrl": "https://wx.qlogo.cn/mmcrhead/xxx/0",
        "CustomizedInfo": {
            "BrandFlag": 0
        },
        "AdditionalContactList": {
            "LinkedinContactItem": {}
        },
        "ChatroomMaxCount": 10007,
        "DeleteFlag": 0,
        "Description": "\b\u0003\u0012\u001c\n\u0013wxid_xxx0\u0001@\u0000\u0001\u0000\u0012\u001c\n\u0013wxid_xxx0\u0001@\u0000\u0001\u0000\u0012\u001c\n\u0013wxid_xxx0\u0001@\u0000\u0001\u0000\u0018\u0001\"\u0000(\u00008\u0000",
        "ChatroomStatus": 5,
        "Extflag": 0,
        "ChatRoomBusinessType": 0
    },
    "Wxid": "wxid_xxx"
}
"""

class XBotMessage(ChatMessage):
    def __init__(self, rawmsg):
        super().__init__(rawmsg)
        self._rawmsg = rawmsg
        # 处理 Data 包装的情况
        if isinstance(rawmsg, dict) and 'Data' in rawmsg:
            msg_data = rawmsg['Data']
        else:
            msg_data = rawmsg
        self.msg_id = self._get_field(msg_data, 'MsgId')
        self.create_time = self._get_field(msg_data, 'CreateTime')
        msg_type = self._get_field(msg_data, 'MsgType', 1)
        self.ctype = ContextType.TEXT
        if msg_type == 1:
            self.ctype = ContextType.TEXT
        elif msg_type == 3:
            self.ctype = ContextType.IMAGE
        elif msg_type == 34:
            self.ctype = ContextType.VOICE
        elif msg_type == 43:
            self.ctype = ContextType.VIDEO
        self.from_user_id = self._get_field(msg_data, 'FromUserName')
        self.to_user_id = self._get_field(msg_data, 'ToUserName')
        self.is_group = False
        self.is_at = False
        self.actual_user_id = None
        self.actual_user_nickname = None
        self.from_user_nickname = None
        self.other_user_id = None
        self.other_user_nickname = None
        self.self_display_name = None
        # 群聊
        if self.from_user_id and "@chatroom" in self.from_user_id:
            self.is_group = True
            self.other_user_id = self.from_user_id  # 群ID
            self.other_user_nickname = self._get_field(msg_data, 'FromNickName') or self.from_user_id
            # 处理群消息内容，提取实际发送者
            content = self._get_field(msg_data, 'Content', '')
            if content and ':' in content:
                parts = content.split(':', 1)
                self.actual_user_id = parts[0].strip()
                self.content = parts[1].strip() if len(parts) > 1 else ''
            else:
                self.content = content
                self.actual_user_id = self.to_user_id  # fallback，防止 produce 误判
            # 尝试获取群成员昵称
            self.actual_user_nickname = self._get_field(msg_data, 'ActualUserNickName') or self.actual_user_id
            self.from_user_nickname = self.other_user_nickname
            msg_source = self._get_field(msg_data, 'MsgSource', '')
            self.is_at = '<atuserlist>' in msg_source and self.to_user_id in msg_source
            self.at_list = []
            if '<atuserlist>' in msg_source:
                at_list_match = re.search(r'<atuserlist><!\[CDATA\[(.*?)\]\]></atuserlist>', msg_source)
                if at_list_match:
                    at_users = at_list_match.group(1)
                    if at_users:
                        self.at_list = [u.strip() for u in at_users.split(',') if u.strip()]
        else:
            # 私聊
            self.content = self._get_field(msg_data, 'Content', '')
            self.from_user_nickname = self._get_field(msg_data, 'FromNickName') or self.from_user_id
            self.other_user_id = self.from_user_id
            self.other_user_nickname = self.from_user_nickname
            self.at_list = []
        self.push_content = self._get_field(msg_data, 'PushContent')
        self.msg_source = self._get_field(msg_data, 'MsgSource')
        self.new_msg_id = self._get_field(msg_data, 'NewMsgId')
        self.receiver = self.from_user_id
        logger.debug(f"[xbot_message] Parsed message: type={self.ctype}, from={self.from_user_id}({self.from_user_nickname}), to={self.to_user_id}, is_group={self.is_group}, is_at={self.is_at}, actual_user_id={self.actual_user_id}, actual_user_nickname={self.actual_user_nickname}, other_user_id={self.other_user_id}, other_user_nickname={self.other_user_nickname}")

    def _get_field(self, data, field_name, default=None):
        """获取字段值，支持嵌套的字典结构"""
        if not data:
            return default
            
        # 直接获取
        if field_name in data:
            value = data[field_name]
            if isinstance(value, dict) and 'string' in value:
                return value['string']
            return value
            
        # 尝试获取小写字段名
        if field_name.lower() in data:
            value = data[field_name.lower()]
            if isinstance(value, dict) and 'string' in value:
                return value['string']
            return value
            
        return default
        
    def download_voice(self):
        try:
            voice_data = self.client.download_voice(self.msg['Wxid'], self.msg_id)
            with open(self.content, "wb") as f:
                f.write(voice_data)
        except Exception as e:
            logger.error(f"[xbot] Failed to download voice file: {e}")

    def download_image(self):
        try:
            # 目前未实现图片下载功能
            logger.warning(f"[xbot] Image download not implemented yet")
        except Exception as e:
            logger.error(f"[xbot] Failed to download image file: {e}")

    def prepare(self):
        if self._prepare_fn:
            self._prepare_fn()

    def _is_non_user_message(self, msg_source: str, from_user_id: str) -> bool:
        """检查消息是否来自非用户账号（如公众号、腾讯游戏、微信团队等）
        
        Args:
            msg_source: 消息的MsgSource字段内容
            from_user_id: 消息发送者的ID
            
        Returns:
            bool: 如果是非用户消息返回True，否则返回False
        """
        # 检查发送者ID
        special_accounts = ["Tencent-Games", "weixin", "filehelper"]
        if from_user_id in special_accounts or from_user_id.startswith("gh_"):
            logger.debug(f"[xbot] non-user message detected by sender id: {from_user_id}")
            return True

        # 检查消息源中的标签
        non_user_indicators = [
            "<tips>3</tips>",
            "<bizmsgshowtype>",
            "</bizmsgshowtype>",
            "<bizmsgfromuser>",
            "</bizmsgfromuser>"
        ]
        if any(indicator in msg_source for indicator in non_user_indicators):
            logger.debug(f"[xbot] non-user message detected by msg_source indicators")
            return True

        # 新增：过滤群聊系统消息/通知/XML
        if self.is_group:
            # 1. 内容是 XML 或以 <msg>、<op>、<sysmsg>、<name>、<arg> 开头
            if isinstance(self.content, str) and self.content.strip().startswith(("<msg", "<op", "<sysmsg", "<name", "<arg")):
                logger.debug(f"[xbot] non-user message detected by xml/system content: {self.content[:30]}")
                return True
            # 2. MsgType 属于系统消息类型
            msg_type = self._get_field(self._rawmsg.get('Data', self._rawmsg), 'MsgType', 1)
            if str(msg_type) in ["10000", "10002", "51", "9999"]:
                logger.debug(f"[xbot] non-user message detected by system MsgType: {msg_type}")
                return True
        return False
