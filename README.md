# xxx-on-wechat (基于 xbot 协议的微信个人号机器人)

> 🚀 支持多平台部署，丰富插件，支持 Web UI，适配 xbot 新协议，Docker 一键部署！

---

## 功能特性

- **xbot 协议微信个人号**：稳定支持扫码登录、消息收发、群聊管理、图片/语音/卡片/链接等多种消息类型。
- **多模型/多通道**：支持 Dify、OpenAI、Coze、Claude、讯飞星火、文心一言等多种大模型。
- **插件化架构**：支持自定义插件，丰富扩展能力。
- **Web UI**：基于 Gradio，支持扫码登录、状态查看、重启、退出等操作。
- **Docker 一键部署**：官方支持多架构镜像，云端/本地一键启动。
- **丰富的配置项**：支持环境变量、config.json 双配置，灵活适配各种场景。

---

## 快速开始

### 1. 环境准备

- Python 3.8+
- pip
- 推荐 Linux/Windows/Mac，支持 x86/arm

### 2. 安装依赖

```bash
pip install -r requirements.txt
# 可选：安装语音、webui等扩展依赖
pip install -r requirements-optional.txt
```

### 3. 配置文件

复制模板并根据实际情况填写：

```bash
cp config-template.json config.json
```

**核心配置示例：**

```json
{
  "channel_type": "xbot",
  "xbot_token": "",
  "xbot_app_id": "",
  "xbot_base_url": "http://127.0.0.1:9011/api",
  "dify_api_base": "https://api.dify.ai/v1",
  "dify_api_key": "app-xxx",
  "model": "dify",
  "web_ui_port": 7860,
  "web_ui_username": "dow",
  "web_ui_password": "dify-on-wechat"
}
```

- `xbot_base_url`：你的 xbot 服务 API 地址
- `xbot_token`/`xbot_app_id`：首次可留空，扫码后自动获取
- 其他参数详见 config-template.json

---

## 启动方式

### 1. 命令行启动

```bash
python app.py
```

### 2. 启动 Web UI

```bash
python web_ui.py
```

- 访问 http://服务器 IP:7860，默认账号/密码：dow/dify-on-wechat

---

## Docker 部署

### 1. 一键启动

```bash
# 先复制配置文件
cp config-template.json config.json

# 推荐使用官方多架构镜像
docker run -itd \
  -v $PWD/config.json:/app/config.json \
  -v $PWD/plugins:/app/plugins \
  -p 7860:7860 \
  -p 9919:9919 \
  --name xbot-wechat \
  --restart=always \
  nanssye/xxx-on-wechat:latest
```

### 2. docker-compose 推荐

```yaml
version: "2.0"
services:
  xbot-on-wechat:
    image: nanssye/xxx-on-wechat:latest
    user: root
    restart: always
    container_name: xbot-on-wechat
    environment:
      TZ: "Asia/Shanghai"
      # DIFY_ON_WECHAT_EXEC: 'python web_ui.py'
      # WEB_UI_PORT: '7860'
      # WEB_UI_USERNAME: 'dow'
      # WEB_UI_PASSWORD: 'dify-on-wechat'
    ports:
      - "7860:7860"
      - "9919:9919"
    volumes:
      - ./config.json:/app/config.json
      - ./plugins:/app/plugins
```

---

## 主要目录结构

```
├── app.py                # 主入口
├── web_ui.py             # Web UI入口
├── config-template.json  # 配置模板
├── channel/xbot/         # xbot协议主通道
├── lib/xbot/             # xbot API客户端
├── plugins/              # 插件目录
├── docker/               # Docker相关
├── requirements.txt
├── requirements-optional.txt
```

---

## xbot 协议简介

- 基于 xbot 协议的微信个人号机器人，支持扫码登录、消息同步、群聊管理、图片/语音/卡片/链接等多种消息类型。
- 通过 `lib/xbot/client.py` 封装 API，支持多种消息收发、联系人管理、群聊管理等。

---

## API 说明（部分）

- **扫码登录**：`/Login/LoginGetQR`
- **消息同步**：`/Msg/SyncMsg`
- **发送文本**：`/Msg/SendTxt`
- **发送图片**：`/Msg/UploadImg`
- **发送语音**：`/Msg/SendVoice`
- **更多 API**：详见 `lib/xbot/client.py`

---

## Web UI 功能

- 扫码登录/退出
- 查看机器人在线状态
- 重启服务
- 支持多用户并发访问

---

## 常见问题

- **扫码后无法登录？**  
  检查 xbot 服务是否正常，配置文件 API 地址是否正确，端口是否开放。
- **消息收发异常？**  
  检查机器人账号是否被风控，是否在目标群聊，API 参数是否正确。
- **图片/语音发送失败？**  
  检查图片/语音格式、大小，API 返回内容，xbot 后端日志。

---

## 贡献与开发

- 支持自定义插件开发，详见 `plugins/` 目录和开发文档。
- 欢迎 PR、Issue、建议！

---

## License

MIT

---

## 架构图

![](docs/gewechat/gewechat_service_design.png)

---

## xbot API 详细说明

### 登录与会话

- **获取二维码**

  - `POST /Login/LoginGetQR`
  - 请求参数：
    ```json
    {
      "DeviceId": "a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "DeviceName": "XBot_12345678"
    }
    ```
  - 返回示例：
    ```json
    {
      "Code": 0,
      "Success": true,
      "Message": "成功",
      "Data": {
        "QrUrl": "https://login.weixin.qq.com/qrcode/xxxxxx==",
        "Uuid": "xxxx-xxxx-xxxx-xxxx"
      }
    }
    ```

- **检查扫码状态**

  - `POST /Login/LoginCheckQR`
  - 请求参数：
    ```json
    { "uuid": "xxxx-xxxx-xxxx-xxxx" }
    ```
  - 返回示例：
    ```json
    {
      "Code": 0,
      "Success": true,
      "Message": "成功",
      "Data": {
        "Status": 1, // 1=已扫码，0=未扫码
        "Wxid": "wxid_xxxxxxx"
      }
    }
    ```

- **唤醒登录**

  - `POST /Login/LoginAwaken`
  - 请求参数：
    ```json
    { "Wxid": "wxid_xxxxxxx" }
    ```
  - 返回示例：
    ```json
    { "Code": 0, "Success": true, "Message": "成功" }
    ```

- **二次登录**

  - `POST /Login/LoginTwiceAutoAuth`
  - 请求参数：
    ```json
    { "wxid": "wxid_xxxxxxx" }
    ```
  - 返回示例：
    ```json
    { "Code": 0, "Success": true, "Message": "成功" }
    ```

- **退出登录**
  - `POST /Login/LogOut`
  - 请求参数：
    ```json
    { "wxid": "wxid_xxxxxxx" }
    ```
  - 返回示例：
    ```json
    { "Code": 0, "Success": true, "Message": "成功" }
    ```

### 消息相关

- **同步消息**

  - `POST /Msg/SyncMsg`
  - 请求参数：
    ```json
    { "wxid": "wxid_xxxxxxx" }
    ```
  - 返回示例（部分）：
    ```json
    {
      "Code": 0,
      "Success": true,
      "Message": "成功",
      "Data": {
        "AddMsgs": [
          {
            "MsgId": 123456,
            "FromUserName": { "string": "wxid_fromuser" },
            "ToUserName": { "string": "wxid_touser" },
            "MsgType": 1,
            "Content": { "string": "你好" },
            "CreateTime": 1733410112
          }
        ]
      }
    }
    ```

- **发送文本消息**

  - `POST /Msg/SendTxt`
  - 请求参数：
    ```json
    {
      "Wxid": "wxid_xxxxxxx",
      "ToWxid": "wxid_target",
      "Content": "你好，世界！",
      "Type": 1
    }
    ```
  - 返回示例：
    ```json
    { "Code": 0, "Success": true, "Message": "成功" }
    ```

- **发送图片消息**

  - `POST /Msg/UploadImg`
  - 请求参数：
    ```json
    {
      "Wxid": "wxid_xxxxxxx",
      "ToWxid": "wxid_target",
      "Base64": "/9j/4AAQSkZJRgABAQAAAQABAAD..." // 图片base64字符串
    }
    ```
  - 返回示例：
    ```json
    { "Code": 0, "Success": true, "Message": "成功", "Data": { ... } }
    ```

- **发送语音消息**

  - `POST /Msg/SendVoice`
  - 请求参数：
    ```json
    {
      "Wxid": "wxid_xxxxxxx",
      "ToWxid": "wxid_target",
      "Base64": "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=", // 语音base64
      "VoiceType": 0,
      "VoiceTime": 1000
    }
    ```
  - 返回示例：
    ```json
    { "Code": 0, "Success": true, "Message": "成功" }
    ```

- **发送卡片/链接/名片/表情/撤回等**
  - 详见 `lib/xbot/client.py`，每个方法均有参数和返回值注释。

### 联系人/群聊管理

- **获取联系人列表**

  - `POST /Contact/GetContacts`
  - 请求参数：
    ```json
    { "wxid": "wxid_xxxxxxx" }
    ```
  - 返回示例（部分）：
    ```json
    {
      "Code": 0,
      "Success": true,
      "Message": "成功",
      "Data": [
        { "Wxid": "wxid_friend1", "NickName": "好友1" },
        { "Wxid": "wxid_friend2", "NickName": "好友2" }
      ]
    }
    ```

- **获取群成员**

  - `POST /Contact/GetGroupMembers`
  - 请求参数：
    ```json
    { "wxid": "wxid_xxxxxxx", "qid": "xxxx@chatroom" }
    ```
  - 返回示例（部分）：
    ```json
    {
      "Code": 0,
      "Success": true,
      "Message": "成功",
      "Data": [
        { "Wxid": "wxid_member1", "NickName": "群成员1" },
        { "Wxid": "wxid_member2", "NickName": "群成员2" }
      ]
    }
    ```

- **加好友/建群/踢人/改群名/公告等**
  - 详见 `lib/xbot/client.py`，每个 API 方法均有参数和返回值注释。

---

## 更多 API 与协议细节

- 推荐直接阅读 `lib/xbot/client.py`，每个 API 方法均有注释和参数说明。
- xbot 后端服务需单独部署，详见 [xbot/gewechat 官方文档](https://github.com/Devo919/Gewechat)。
