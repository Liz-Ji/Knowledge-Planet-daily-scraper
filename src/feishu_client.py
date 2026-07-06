"""飞书多维表格写入客户端：tenant_access_token 缓存 + 批量写入。"""
import time
import requests

OPEN_BASE = "https://open.feishu.cn/open-apis"


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, app_token: str, table_id: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self._token = None
        self._token_expire_at = 0

    def _get_tenant_token(self) -> str:
        if self._token and time.time() < self._token_expire_at:
            return self._token
        resp = requests.post(
            f"{OPEN_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")
        self._token = data["tenant_access_token"]
        # 提前 5 分钟刷新
        self._token_expire_at = time.time() + data.get("expire", 7200) - 300
        return self._token

    def _auth_header(self):
        # GET 请求不带 Content-Type，否则飞书会尝试解析空 body 返回 400
        return {"Authorization": f"Bearer {self._get_tenant_token()}"}

    def _json_headers(self):
        return {
            "Authorization": f"Bearer {self._get_tenant_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _table_url(self, suffix: str = "") -> str:
        return f"{OPEN_BASE}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records{suffix}"

    def get_existing_topic_ids(self, page_size: int = 500) -> set:
        """拉取表中已有的全部「帖子ID」，用于去重（数据量大时建议改用本地状态文件辅助）。"""
        ids = set()
        page_token = None
        page_size = min(page_size, 500)
        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(
                self._table_url(), headers=self._auth_header(), params=params, timeout=15
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"读取飞书表格记录失败: {data}")
            for item in data["data"].get("items", []):
                val = item.get("fields", {}).get("帖子ID")
                if val:
                    ids.add(str(val))
            if not data["data"].get("has_more"):
                break
            page_token = data["data"].get("page_token")
        return ids

    def batch_create_records(self, records: list):
        """records: 每项为 {字段名: 值} 的 dict 列表，一次最多 500 条。"""
        if not records:
            return
        for i in range(0, len(records), 500):
            chunk = records[i : i + 500]
            body = {"records": [{"fields": r} for r in chunk]}
            resp = requests.post(
                self._table_url("/batch_create"),
                headers=self._json_headers(),
                json=body,
                timeout=20,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"写入飞书表格失败: {data}")

    def list_all_records(self, page_size: int = 500):
        """遍历全部记录，返回 [{record_id, fields}, ...]。"""
        items = []
        page_token = None
        page_size = min(page_size, 500)
        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(
                self._table_url(), headers=self._auth_header(), params=params, timeout=15
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"读取飞书表格记录失败: {data}")
            items.extend(data["data"].get("items", []))
            if not data["data"].get("has_more"):
                break
            page_token = data["data"].get("page_token")
        return items

    def batch_update_records(self, updates: list):
        """updates: [{"record_id":..., "fields":{...}}, ...]，一次最多 500 条。"""
        if not updates:
            return
        for i in range(0, len(updates), 500):
            chunk = updates[i : i + 500]
            body = {"records": [{"record_id": u["record_id"], "fields": u["fields"]} for u in chunk]}
            resp = requests.post(
                self._table_url("/batch_update"),
                headers=self._json_headers(),
                json=body,
                timeout=20,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"更新飞书表格失败: {data}")
