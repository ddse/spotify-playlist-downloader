import unittest

from web.app import _image_proxy_url, _is_allowed_outlink_host, _rewrite_outlinks


class ImageProxyTests(unittest.TestCase):
    def test_image_proxy_url_rewrites_nct_cdn(self):
        source = "https://image-cdn.nct.vn/song/2024/08/23/e/c/3/c/1724415120899_300.jpg"
        result = _image_proxy_url(source)

        self.assertTrue(result.startswith("/api/image-proxy?url="))
        self.assertIn("image-cdn.nct.vn", result)

    def test_image_proxy_url_rejects_unknown_host(self):
        self.assertEqual(_image_proxy_url("https://example.com/image.jpg"), "")

    def test_allowed_host_supports_spotify_image_cdn(self):
        self.assertTrue(_is_allowed_outlink_host("i.scdn.co"))

    def test_spotify_thumbnail_is_non_null_proxy(self):
        result = _rewrite_outlinks({
            "image": "https://i.scdn.co/image/ab67616d00001e02abcdef1234567890"
        })
        self.assertTrue(result["image"].startswith("/api/image-proxy?url="))
        self.assertNotEqual(result["image"], "")

    def test_allowed_host_supports_zing_and_youtube_image_cdns(self):
        self.assertTrue(_is_allowed_outlink_host("photo-resize-zmp3.zmdcdn.me"))
        self.assertTrue(_is_allowed_outlink_host("i.ytimg.com"))

    def test_allowed_host_supports_nct_subdomains(self):
        self.assertTrue(_is_allowed_outlink_host("image-cdn.nct.vn"))
        self.assertTrue(_is_allowed_outlink_host("nct.vn"))
        self.assertFalse(_is_allowed_outlink_host("evil.example.com"))

    def test_rewrite_outlinks_uses_image_proxy_for_image_fields(self):
        source = "https://image-cdn.nct.vn/song/test.jpg"
        result = _rewrite_outlinks({
            "image": source,
            "thumbnail": source,
            "streamURL": source,
        })

        self.assertTrue(result["image"].startswith("/api/image-proxy?url="))
        self.assertTrue(result["thumbnail"].startswith("/api/image-proxy?url="))
        self.assertTrue(result["streamURL"].startswith("/api/outlink?url="))


if __name__ == "__main__":
    unittest.main()
