"""异常提醒：通过飞书群机器人 Webhook 推送文本消息（例如 Cookie 失效）。"""
import logging
import requests


def send_alert(webhook_url: str, text: str):
    if not webhook_url:
        return
    try:
        requests.post(
            webhook_url,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=10,
        )
    except requests.RequestException:
        logging.exception("发送 Webhook 提醒失败")
