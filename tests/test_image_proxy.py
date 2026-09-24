import unittest

from web.app import _image_proxy_url, _is_allowed_outlink_host, _rewrite_outlinks, _search_result_for_ui


class ImageProxyTests(unittest.TestCase):
    def test_image_proxy_url_rewrites_nct_cdn(self):
        source = "https://image-cdn.nct.vn/song/2024/08/23/e/c/3/c/1724415120899_300.jpg"
        result = _image_proxy_url(source)

        self.assertTrue(result.startswith("/api/image-proxy?url="))
        self.assertIn("image-cdn.nct.vn", result)

    def test_image_proxy_url_rejects_unknown_host(self):
        self.assertEqual(_image_proxy_url("https://example.com/image.jpg"), "")

    def test_allowed_host_supports_zing_and_youtube_image_cdns(self):
        self.assertTrue(_is_allowed_outlink_host("photo-resize-zmp3.zmdcdn.me"))
        self.assertTrue(_is_allowed_outlink_host("i.ytimg.com"))

    def test_allowed_host_supports_nct_subdomains(self):
        self.assertTrue(_is_allowed_outlink_host("image-cdn.nct.vn"))
        self.assertTrue(_is_allowed_outlink_host("nct.vn"))
        self.assertFalse(_is_allowed_outlink_host("evil.example.com"))

    def test_rewrite_outlinks_uses_image_proxy_for_all_image_fields(self):
        source = "https://image-cdn.nct.vn/song/test.jpg"
        result = _rewrite_outlinks({
            "image": source,
            "thumbnail": source,
            "thumbnailUrl": source,
            "thumbnailM": source,
            "imageUrl": source,
            "coverUrl": source,
            "avatarUrl": source,
            "streamURL": source,
        })

        for key in ("image", "thumbnail", "thumbnailUrl", "thumbnailM", "imageUrl", "coverUrl", "avatarUrl"):
            self.assertTrue(
                result[key].startswith("/api/image-proxy?url="),
                f"{key} was not proxied: {result[key]!r}",
            )
        self.assertTrue(result["streamURL"].startswith("/api/outlink?url="))

    def test_rewrite_outlinks_blanks_unsupported_remote_images(self):
        result = _rewrite_outlinks({
            "image": "https://example.com/image.jpg",
            "thumbnail": "https://example.com/thumb.jpg",
        })
        self.assertEqual(result["image"], "")
        self.assertEqual(result["thumbnail"], "")

    def test_search_result_for_ui_returns_non_null_nct_image_proxy(self):
        source = "https://image-cdn.nct.vn/singer/avatar/example.jpg"
        result = _search_result_for_ui({
            "items": [{
                "id": "LX0XVH77VeER",
                "title": "Hoa Vô Sắc",
                "thumbnail": source,
                "source": "nhaccuatui",
            }]
        })
        thumbnail = result["items"][0]["thumbnail"]
        self.assertIsInstance(thumbnail, str)
        self.assertTrue(thumbnail.startswith("/api/image-proxy?url="))
        self.assertNotEqual(thumbnail, "")
        self.assertIn("image-cdn.nct.vn", thumbnail)

    def test_rewrite_outlinks_preserves_existing_relative_image_proxy(self):
        value = "/api/image-proxy?url=https%3A%2F%2Fimage-cdn.nct.vn%2Fsong%2Ftest.jpg"
        result = _rewrite_outlinks({"image": value})
        self.assertEqual(result["image"], value)


if __name__ == "__main__":
    unittest.main()
