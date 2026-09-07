import unittest

from src.processors.archive_docs import docs_to_archive


class TestDocsToArchive(unittest.TestCase):
    def test_collects_agenda_packet_and_nonempty_minutes(self):
        docs = docs_to_archive(
            {
                "agenda_packet": "https://x/packet.pdf",
                "minutes_and_supplemental_materials": {
                    "Staff Report": "https://x/s.pdf",
                    "Empty": "",
                },
            }
        )
        self.assertEqual(
            docs,
            {
                "Agenda Packet": "https://x/packet.pdf",
                "Staff Report": "https://x/s.pdf",
            },
        )

    def test_empty_when_no_documents(self):
        self.assertEqual(docs_to_archive({"video": "https://x/v.mp4"}), {})


if __name__ == "__main__":
    unittest.main()
