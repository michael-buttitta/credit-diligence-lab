import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

import analyze
from evaluate import amounts_millions
from retrieval import FilingIndex


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = FilingIndex()

    def test_full_filing_has_contiguous_source_lines(self):
        passages = self.index.passages
        self.assertEqual([item["pdf_page"] for item in passages], list(range(5, 108)))
        expected_line = 1
        for passage in passages:
            self.assertEqual(passage["start_line"], expected_line)
            expected_line += len(passage["lines"])
        debt = next(item for item in passages if item["pdf_page"] == 81)
        self.assertIn("2026 $ 2,000", debt["lines"])
        self.assertIn("2027 1,000", debt["lines"])

    def test_curated_answerable_questions_retrieve_expected_pages(self):
        path = Path(__file__).resolve().parents[1] / "evaluation" / "questions.json"
        for case in json.loads(path.read_text()):
            pages = {item["pdf_page"] for item in self.index.retrieve(case["question"])}
            for group in case["required_page_groups"]:
                with self.subTest(question=case["id"]):
                    self.assertTrue(pages.intersection(group))

    def test_maturity_paraphrase_retrieves_schedule(self):
        pages = {item["pdf_page"] for item in self.index.retrieve("What debt matures over the next two years?")}
        self.assertIn(81, pages)
        self.assertEqual(self.index.retrieve("zzzxxyy"), [])

    def test_retrieval_cli_runs_from_another_directory_without_credentials(self):
        script = Path(__file__).resolve().parents[1] / "analyze.py"
        environment = dict(os.environ)
        environment.pop("OPENAI_API_KEY", None)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(script), "--retrieve-only", "What revolving credit facilities expire in 2026?"],
                cwd=directory, env=environment, capture_output=True, text=True, check=True,
            )
        context = json.loads(result.stdout)
        self.assertIn(81, {passage["pdf_page"] for passage in context["passages"]})
        self.assertEqual(len(context["lines"]), len(context["line_numbers"]))

    def test_cannot_cite_a_document_line_outside_retrieved_context(self):
        answer = {
            "status": "answered", "answer": "example", "evidence": [{"line": 2, "quote": "available"}],
        }
        response = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}],
        }
        with self.assertRaisesRegex(ValueError, "could not be verified"):
            analyze.read_answer(response, ["available"], [100])
        answer["evidence"][0]["line"] = 100
        response["output"][0]["content"][0]["text"] = json.dumps(answer)
        self.assertEqual(analyze.read_answer(response, ["available"], [100]), answer)

    def test_evaluation_accepts_equivalent_dollar_units(self):
        self.assertIn(3000, amounts_millions("USD 3,000 million"))
        self.assertIn(3000, amounts_millions("$3.0 billion"))
        self.assertIn(126843, amounts_millions("$126,843 million"))


if __name__ == "__main__":
    unittest.main()
