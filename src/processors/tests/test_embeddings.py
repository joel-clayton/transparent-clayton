import unittest
from unittest.mock import patch

from src.processors.helpers import embeddings


class TestEmbeddingPrefixes(unittest.TestCase):
    def test_nomic_document_and_query_prefixes(self):
        with patch.object(embeddings, "embed", return_value=[0.0]) as embed:
            embeddings.embed_document("hello", model="nomic-embed-text")
            embeddings.embed_query("hello", model="nomic-embed-text")
        self.assertEqual(
            embed.call_args_list[0].args, ("nomic-embed-text", "search_document: hello")
        )
        self.assertEqual(
            embed.call_args_list[1].args, ("nomic-embed-text", "search_query: hello")
        )

    def test_unknown_model_gets_no_prefix(self):
        with patch.object(embeddings, "embed", return_value=[0.0]) as embed:
            embeddings.embed_document("hello", model="some-future-model")
        self.assertEqual(embed.call_args.args, ("some-future-model", "hello"))

    def test_prefixes_are_model_aware(self):
        self.assertEqual(
            embeddings.prefixes_for("nomic-embed-text")[0], "search_document: "
        )
        self.assertEqual(embeddings.prefixes_for("bge-large-en-v1.5")[0], "")
        self.assertTrue(
            embeddings.prefixes_for("bge-large-en-v1.5")[1].startswith("Represent")
        )
        self.assertEqual(
            embeddings.prefixes_for("multilingual-e5-large"), ("passage: ", "query: ")
        )


if __name__ == "__main__":
    unittest.main()
