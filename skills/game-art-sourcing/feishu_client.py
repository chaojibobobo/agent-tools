"""飞书开放平台 API 客户端 — 支持 HTML 导入和 Block 方式创建文档"""

from __future__ import annotations

import io
import time
import json
import logging
import requests

logger = logging.getLogger("wechat2feishu")


class FeishuClient:
    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str, app_secret: str, folder_token: str = "",
                 user_open_id: str = "", webhook_url: str = "",
                 doc_domain: str = "open.feishu.cn",
                 grant_user_access: bool = False):
        self.app_id = app_id
        self.app_secret = app_secret
        self.folder_token = folder_token
        self.user_open_id = user_open_id
        self.webhook_url = webhook_url
        self.doc_domain = doc_domain
        self.grant_user_access = grant_user_access
        self._token = ""
        self._token_expires_at = 0

    def _ensure_token(self):
        if self._token and time.time() < self._token_expires_at - 60:
            return
        resp = requests.post(
            f"{self.BASE_URL}/auth/v3/tenant_access_token/internal/",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 token 失败: {data}")
        self._token = data["tenant_access_token"]
        self._token_expires_at = time.time() + data.get("expire", 7200)

    @property
    def _headers(self):
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}"}

    # ── 方式1：HTML 导入 ──────────────────────────────────────

    def import_html(self, html_content: str, title: str) -> str:
        """上传 HTML 文件并导入为飞书文档。返回文档 URL。"""
        # Step 1: 上传 HTML 文件到云空间
        file_token = self._upload_file(html_content.encode("utf-8"), f"{title}.html")
        logger.info("文件上传成功: %s", file_token)

        # Step 2: 创建导入任务
        import_body = {
            "file_extension": "html",
            "file_token": file_token,
            "type": "docx",
            "file_name": title,
        }
        if self.folder_token:
            import_body["point"] = {"folder_token": self.folder_token}

        resp = requests.post(
            f"{self.BASE_URL}/drive/v1/import_tasks",
            headers={**self._headers, "Content-Type": "application/json"},
            json=import_body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建导入任务失败: {data}")
        ticket = data["data"]["ticket"]
        logger.info("导入任务已创建: %s", ticket)

        # Step 3: 轮询导入结果
        return self._wait_for_import(ticket)

    def _upload_file(self, file_data: bytes, filename: str) -> str:
        """上传文件到飞书云空间，返回 file_token"""
        self._ensure_token()
        resp = requests.post(
            f"{self.BASE_URL}/drive/v1/files/upload_all",
            headers={"Authorization": f"Bearer {self._token}"},
            data={
                "parent_node": self.folder_token,
                "parent_type": "explorer",
            },
            files={"file": (filename, file_data, "text/html")},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"上传文件失败: {data}")
        return data["data"]["file_token"]

    def _wait_for_import(self, ticket: str, max_wait: int = 120) -> str:
        """轮询导入任务直到完成"""
        start = time.time()
        while time.time() - start < max_wait:
            resp = requests.get(
                f"{self.BASE_URL}/drive/v1/import_tasks/{ticket}",
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"查询导入任务失败: {data}")

            job = data["data"].get("result", {})
            job_status = job.get("job_status", "")

            if job_status == "success":
                token = job.get("token", "")
                logger.info("导入成功！")
                return self._build_doc_url(token)
            elif job_status in ("failed", "init failed"):
                raise RuntimeError(f"导入失败: {json.dumps(job, ensure_ascii=False)}")
            # 0 or "processing" — 继续等待
            time.sleep(2)

        raise RuntimeError(f"导入超时 ({max_wait}s)")

    # ── 方式2：Block 方式创建文档（支持图片）───────────────────

    def create_document(self, title: str) -> str:
        """创建空飞书文档，返回 document_id"""
        self._ensure_token()
        body = {"title": title}
        if self.folder_token:
            body["folder_token"] = self.folder_token
        resp = requests.post(
            f"{self.BASE_URL}/docx/v1/documents",
            headers={**self._headers, "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建文档失败: {data}")
        doc_id = data["data"]["document"]["document_id"]

        # 给用户授权 full_access
        if self.user_open_id and self.grant_user_access:
            self._grant_permission(doc_id, self.user_open_id)

        return doc_id

    def create_blocks(self, doc_id: str, blocks: list[dict],
                      image_map: dict[int, bytes] | None = None,
                      image_alternates: dict[int, list[bytes]] | None = None) -> dict:
        """将 Block 列表写入文档，处理图片上传。

        非图片块按顺序批量写入。图片块先创建空块拿到 block_id，
        再并行上传素材 + patch，减少总耗时。

        Args:
            doc_id: 文档 ID
            blocks: 飞书 Block 列表
            image_map: {block_index: image_bytes} 图片块对应的图片二进制数据
            image_alternates: 同一分桶的备用图片；主图重试一次仍失败后替换

        Returns:
            可序列化的图片写入回执；失败图片块会被删除，不静默留空。
        """
        if image_map is None:
            image_map = {}
        if image_alternates is None:
            image_alternates = {}

        image_indices = set(image_map.keys())
        batch = []

        def flush_batch():
            if not batch:
                return
            for start in range(0, len(batch), 50):
                chunk = batch[start:start + 50]
                self._append_blocks(doc_id, chunk)
            batch.clear()

        # Phase 1: write text blocks in order, create empty image blocks
        image_block_ids: dict[int, str] = {}
        failures: list[dict] = []
        for i, block in enumerate(blocks):
            if block.get("block_type") == 27:
                flush_batch()
                if i not in image_indices:
                    failures.append({
                        "index": i,
                        "reason": "image_payload_missing",
                    })
                    logger.warning("图片块缺少有效素材，已跳过 (block %d)", i)
                    continue
                bid = self._append_single_block(doc_id, {"block_type": 27, "image": {}})
                if bid:
                    image_block_ids[i] = bid
                else:
                    failures.append({
                        "index": i,
                        "reason": "image_block_create_failed",
                    })
                time.sleep(0.35)
            else:
                batch.append(block)
        flush_batch()

        # Phase 2: upload media + patch in parallel
        results: list[dict] = []
        if image_block_ids:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {}
                for idx, bid in image_block_ids.items():
                    payloads = [image_map[idx], *image_alternates.get(idx, [])]
                    futures[pool.submit(
                        self._fill_image_with_fallback,
                        doc_id,
                        bid,
                        payloads,
                        idx,
                    )] = idx
                for f in as_completed(futures):
                    idx = futures[f]
                    try:
                        result = f.result()
                        if result.get("success"):
                            results.append(result)
                        else:
                            failures.append(result)
                    except Exception as e:
                        logger.warning("图片并行处理失败 (block %d): %s", idx, e)
                        failures.append({
                            "index": idx,
                            "block_id": image_block_ids.get(idx),
                            "reason": str(e),
                        })

        failed_block_ids = [
            item.get("block_id") for item in failures if item.get("block_id")
        ]
        if failed_block_ids:
            if not self._delete_child_blocks(doc_id, failed_block_ids):
                failures.append({
                    "reason": "empty_block_delete_failed",
                    "block_ids": failed_block_ids,
                })

        self._verify_document_content(doc_id, len(blocks), image_indices)
        written_indices = sorted(item["index"] for item in results)
        return {
            "requested_images": len(image_indices),
            "written_images": len(written_indices),
            "written_indices": written_indices,
            "replacements": sum(1 for item in results if item.get("replacement")),
            "failures": failures,
        }

    def _fill_image_with_fallback(self, doc_id: str, block_id: str,
                                  payloads: list[bytes], index: int) -> dict:
        """Retry once, then try same-bucket alternate payloads in order."""
        errors = []
        for payload_index, payload in enumerate(payloads):
            for attempt in range(2):
                try:
                    self._fill_image(doc_id, block_id, payload)
                    return {
                        "index": index,
                        "success": True,
                        "replacement": payload_index > 0,
                        "attempts": len(errors) + 1,
                    }
                except Exception as error:
                    errors.append(str(error))
                    logger.warning(
                        "图片上传失败 (block %d, candidate %d, attempt %d/2): %s",
                        index,
                        payload_index + 1,
                        attempt + 1,
                        error,
                    )
                    if attempt == 0:
                        time.sleep(0.35)
        return {
            "index": index,
            "block_id": block_id,
            "success": False,
            "reason": "upload_failed_after_retry_and_fallback",
            "errors": errors,
        }

    def _verify_document_content(self, doc_id: str, total_blocks: int,
                                  image_indices: set[int]) -> None:
        """验证文档内容不为空，为空则抛异常"""
        try:
            resp = requests.get(
                f"{self.BASE_URL}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                headers=self._headers,
                params={"page_size": 1},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                logger.warning("文档内容校验失败: %s", data.get("msg"))
                return
            children = data.get("data", {}).get("items", [])
            if not children:
                raise RuntimeError(
                    f"文档内容为空! 总共 {total_blocks} 个 block (含 {len(image_indices)} 张图片) "
                    f"但写入后文档无任何内容，请检查 API 调用是否全部失败。"
                )
            logger.info("文档内容校验通过: 文档已有内容 (写入 %d 块)", total_blocks)
        except requests.RequestException as e:
            logger.warning("文档内容校验请求失败: %s", e)

    def _append_blocks(self, doc_id: str, blocks: list[dict]) -> list[str | None]:
        """向文档追加多个 block，返回 block_id 列表"""
        resp = requests.post(
            f"{self.BASE_URL}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
            headers={**self._headers, "Content-Type": "application/json"},
            json={"children": blocks, "index": -1},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("批量写入 Block 失败: %s", data.get("msg"))
            # 逐个重试
            ids = []
            for block in blocks:
                ids.append(self._append_single_block(doc_id, block))
            return ids
        children = data.get("data", {}).get("children", [])
        return [c.get("block_id") for c in children]

    def prepend_blocks(self, doc_id: str, blocks: list[dict]) -> list[str | None]:
        """Insert blocks at the document top without changing the main flow."""
        if not blocks:
            return []
        resp = requests.post(
            f"{self.BASE_URL}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
            headers={**self._headers, "Content-Type": "application/json"},
            json={"children": blocks, "index": 0},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"插入顶部告警失败: {data.get('msg')}")
        return [item.get("block_id") for item in data.get("data", {}).get("children", [])]

    def _append_single_block(self, doc_id: str, block: dict) -> str | None:
        """向文档追加单个 block，返回 block_id 或 None"""
        resp = requests.post(
            f"{self.BASE_URL}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
            headers={**self._headers, "Content-Type": "application/json"},
            json={"children": [block], "index": -1},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("写入 Block 失败 (type=%s): %s", block.get("block_type"), data.get("msg"))
            return None
        children = data.get("data", {}).get("children", [])
        if children:
            return children[0].get("block_id")
        return None

    def _create_and_fill_image(self, doc_id: str, img_data: bytes) -> str | None:
        """Legacy serial path: create empty block → upload → patch."""
        empty_img_block = {"block_type": 27, "image": {}}
        img_block_id = self._append_single_block(doc_id, empty_img_block)
        if not img_block_id:
            logger.warning("创建空图片块失败，跳过此图片")
            return None
        time.sleep(0.35)
        self._fill_image(doc_id, img_block_id, img_data)
        return img_block_id

    def _fill_image(self, doc_id: str, block_id: str, img_data: bytes) -> None:
        """Upload media + patch into an existing empty image block."""
        file_token = self._upload_media(img_data, block_id, "docx_image")
        resp = requests.patch(
            f"{self.BASE_URL}/docx/v1/documents/{doc_id}/blocks/{block_id}",
            headers={**self._headers, "Content-Type": "application/json"},
            json={"replace_image": {"token": file_token}},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"设置图片素材失败: {data.get('msg')}")
        logger.debug("图片填充成功: %s", block_id)

    def _delete_block(self, doc_id: str, block_id: str) -> bool:
        """Compatibility wrapper for deleting one failed child block."""
        return self._delete_child_blocks(doc_id, [block_id])

    def _list_child_blocks(self, doc_id: str) -> list[dict]:
        items = []
        page_token = ""
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(
                f"{self.BASE_URL}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                headers=self._headers,
                params=params,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"读取文档子块失败: {data.get('msg')}")
            page = data.get("data", {})
            items.extend(page.get("items", []))
            if not page.get("has_more"):
                return items
            page_token = page.get("page_token") or ""

    def _delete_child_blocks(self, doc_id: str, block_ids: list[str]) -> bool:
        """Delete failed placeholders using Feishu's parent range API.

        Feishu deletes children by parent-relative [start, end) ranges, not by
        issuing DELETE directly against the child block ID. Ranges are removed
        from the end so earlier indices remain stable.
        """
        try:
            wanted = set(block_ids)
            children = self._list_child_blocks(doc_id)
            positions = sorted(
                [index for index, item in enumerate(children) if item.get("block_id") in wanted],
                reverse=True,
            )
            ranges = []
            for position in positions:
                if ranges and position + 1 == ranges[-1][0]:
                    ranges[-1] = (position, ranges[-1][1])
                else:
                    ranges.append((position, position + 1))
            for start_index, end_index in ranges:
                resp = requests.delete(
                    f"{self.BASE_URL}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children/batch_delete",
                    headers={**self._headers, "Content-Type": "application/json"},
                    params={"document_revision_id": -1},
                    json={"start_index": start_index, "end_index": end_index},
                    timeout=20,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 0:
                    raise RuntimeError(data.get("msg") or "unknown delete error")
                time.sleep(0.35)
            return True
        except Exception as error:
            logger.warning("删除空图片块请求失败 (%s): %s", ",".join(block_ids), error)
            return False

    def get_document_image_count(self, doc_id: str) -> int:
        """Read back the actual number of filled image blocks."""
        return sum(
            1 for item in self._list_child_blocks(doc_id)
            if item.get("block_type") == 27 and item.get("image", {}).get("token")
        )

    def _upload_media(self, file_data: bytes, parent_node: str,
                      parent_type: str = "docx_image",
                      filename: str = "image.png") -> str:
        """上传素材到飞书云文档，返回 file_token"""
        self._ensure_token()
        resp = requests.post(
            f"{self.BASE_URL}/drive/v1/medias/upload_all",
            headers={"Authorization": f"Bearer {self._token}"},
            data={
                "file_name": filename,
                "parent_type": parent_type,
                "parent_node": parent_node,
                "size": str(len(file_data)),
            },
            files={"file": (filename, file_data)},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"上传素材失败: {data}")
        return data["data"]["file_token"]

    def _grant_permission(self, token: str, open_id: str,
                          perm: str = "full_access") -> None:
        """给用户授权文档权限"""
        try:
            resp = requests.post(
                f"{self.BASE_URL}/drive/v1/permissions/{token}/members",
                headers={**self._headers, "Content-Type": "application/json"},
                params={"type": "docx"},
                json={
                    "member_type": "openid",
                    "member_id": open_id,
                    "perm": perm,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.warning("授权失败: %s (code=%s)", data.get("msg"), data.get("code"))
            else:
                logger.info("已授权用户 full_access 权限")
        except Exception as e:
            logger.warning("授权请求失败: %s", e)

    # ── 消息通知（IM 私信 + Webhook 回退）────────────────────

    def send_article_notification(self, account_name: str,
                                   articles: list[dict]) -> dict:
        """发送富文本文章通知，优先 IM 私信，回退 Webhook"""
        content_lines = [
            [{"tag": "text", "text": f"来源: {account_name}\n"}],
        ]
        for i, art in enumerate(articles, 1):
            title = art.get("title", "无标题")
            url = art.get("url", "")
            content_lines.append([
                {"tag": "text", "text": f"{i}. "},
                {"tag": "a", "text": title, "href": url},
            ])

        # 优先 IM 私信
        if self.user_open_id:
            return self._send_im_notification(content_lines, len(articles))
        # 回退 Webhook
        if self.webhook_url:
            return self._send_webhook_notification(content_lines, len(articles))

        logger.warning("未配置 user_open_id 和 webhook_url，跳过通知")
        return {}

    def _send_im_notification(self, content_lines: list, count: int) -> dict:
        """通过 IM API 发送私信"""
        post_content = {
            "zh_cn": {
                "title": f"发现 {count} 篇新文章",
                "content": content_lines,
            }
        }
        resp = requests.post(
            f"{self.BASE_URL}/im/v1/messages",
            headers={**self._headers, "Content-Type": "application/json"},
            params={"receive_id_type": "open_id"},
            json={
                "receive_id": self.user_open_id,
                "msg_type": "post",
                "content": json.dumps(post_content),
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("IM 通知失败: %s", data.get("msg"))
        else:
            logger.info("IM 私信通知成功")
        return data

    def _send_webhook_notification(self, content_lines: list, count: int) -> dict:
        """通过 Webhook 发送群消息"""
        body = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": f"发现 {count} 篇新文章",
                        "content": content_lines,
                    }
                }
            }
        }
        resp = requests.post(self.webhook_url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("Webhook 通知失败: %s", data.get("msg"))
        else:
            logger.info("Webhook 通知成功")
        return data

    def create_document_with_content(self, title: str, full_html: str, **kwargs) -> str:
        """从完整 HTML 创建飞书文档（优先使用 HTML 导入，失败则回退到 Block 方式）"""
        try:
            return self.import_html(full_html, title)
        except Exception as e:
            logger.warning("HTML 导入失败: %s，回退到 Block 方式", e)
            from converter import html_to_blocks
            from fetcher import HEADERS
            blocks, image_map = html_to_blocks(full_html)
            doc_id = self.create_document(title)
            # 下载图片数据
            img_data_map = {}
            for idx, src_url in image_map.items():
                try:
                    resp = requests.get(src_url, headers=HEADERS, timeout=30)
                    resp.raise_for_status()
                    img_data_map[idx] = resp.content
                except Exception as img_err:
                    logger.warning("下载图片失败 [%s]: %s", src_url, img_err)
            self.create_blocks(doc_id, blocks, img_data_map)
            return self._build_doc_url(doc_id)

    def get_document_url(self, document_id: str) -> str:
        return self._build_doc_url(document_id)

    def _build_doc_url(self, token: str) -> str:
        return f"https://{self.doc_domain}/docx/{token}"
