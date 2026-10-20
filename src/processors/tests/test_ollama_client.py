import unittest
from unittest.mock import MagicMock, patch

from src.processors.helpers import ollama_client


class TestOllamaClient(unittest.TestCase):
    def test_generate_posts_and_returns_response(self):
        resp = MagicMock()
        resp.json.return_value = {"response": "hello"}
        with patch.object(ollama_client.requests, "post", return_value=resp) as post:
            out = ollama_client.generate("m", "prompt text")
        self.assertEqual(out, "hello")
        (url,) = post.call_args.args
        self.assertTrue(url.endswith("/api/generate"))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["prompt"], "prompt text")
        self.assertFalse(body["stream"])
        resp.raise_for_status.assert_called_once()

    def test_embed_returns_vector(self):
        resp = MagicMock()
        resp.json.return_value = {"embedding": [0.1, 0.2]}
        with patch.object(ollama_client.requests, "post", return_value=resp) as post:
            vec = ollama_client.embed("e", "text")
        self.assertEqual(vec, [0.1, 0.2])
        self.assertTrue(post.call_args.args[0].endswith("/api/embeddings"))

    def test_generate_passes_optional_knobs(self):
        resp = MagicMock()
        resp.json.return_value = {"response": "ok"}
        with patch.object(ollama_client.requests, "post", return_value=resp) as post:
            ollama_client.generate(
                "m", "p", system="sys", think=False, format="json", options={"seed": 1}
            )
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["system"], "sys")
        self.assertIs(body["think"], False)
        self.assertEqual(body["format"], "json")
        self.assertEqual(body["options"], {"seed": 1})

    def test_unload_posts_keep_alive_zero(self):
        resp = MagicMock()
        with patch.object(ollama_client.requests, "post", return_value=resp) as post:
            ollama_client.unload("m")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["keep_alive"], 0)
        self.assertTrue(post.call_args.args[0].endswith("/api/generate"))
        resp.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
