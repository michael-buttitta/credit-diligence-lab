"""Local BM25 retrieval over page passages, with stable document line numbers."""

from collections import Counter
import json
import math
from pathlib import Path
import re

CORPUS_PATH = Path(__file__).resolve().parent / "data" / "alphabet-2025-10k.json"
STOP_WORDS = set(
    "a an and are as at be by did do does for from had has have how i in is it its "
    "of on or our over report reported reporting s should that the their there these this "
    "to was we were what when which who with would year years end ended".split()
)
TERM_EQUIVALENTS = {
    "mature": "maturity", "matures": "maturity", "maturing": "maturity",
    "maturities": "maturity", "revolver": "revolving", "revolvers": "revolving",
    "facilities": "facility", "expires": "expire", "expiration": "expire",
    "expirations": "expire",
}


def tokenize(text):
    text = text.casefold()
    words = [TERM_EQUIVALENTS.get(word, word) for word in re.findall(r"[a-z0-9]+", text) if word not in STOP_WORDS]
    if re.search(r"\bprincipal payments\b", text):
        words.append("maturity")
    return words


class FilingIndex:
    def __init__(self, path=CORPUS_PATH):
        self.corpus = json.loads(path.read_text(encoding="utf-8"))
        self.passages = self.corpus["passages"]
        if self.corpus["format_version"] != 1 or not self.passages:
            raise ValueError("Unsupported or empty filing corpus. Run ingest.py.")
        self.counts = [
            Counter(tokenize(passage["section"] + " " + " ".join(passage["lines"])))
            for passage in self.passages
        ]
        self.lengths = [sum(count.values()) for count in self.counts]
        self.average_length = sum(self.lengths) / len(self.lengths)
        self.document_frequency = Counter(word for count in self.counts for word in count)

    def retrieve(self, question, top_k=5):
        if not 1 <= top_k <= 10:
            raise ValueError("top_k must be between 1 and 10.")
        terms = set(tokenize(question))
        if {"debt", "maturity"}.issubset(terms):
            terms.update(("principal", "payments"))
        scored = []
        count = len(self.passages)
        for passage, frequencies, length in zip(self.passages, self.counts, self.lengths):
            score = 0.0
            normalization = 1.5 * (0.25 + 0.75 * length / self.average_length)
            for term in terms:
                frequency = frequencies[term]
                inverse_frequency = math.log(1 + (count - self.document_frequency[term] + 0.5) / (self.document_frequency[term] + 0.5))
                score += inverse_frequency * frequency * 2.5 / (frequency + normalization)
            if score > 0:
                scored.append({**passage, "score": round(score, 6)})
        return sorted(scored, key=lambda passage: (-passage["score"], passage["pdf_page"]))[:top_k]

    def context(self, question, top_k=5):
        passages = self.retrieve(question, top_k)
        numbered = {}
        for passage in passages:
            numbered.update(
                (passage["start_line"] + offset, line)
                for offset, line in enumerate(passage["lines"])
            )
        numbers = sorted(numbered)
        return {
            "lines": [numbered[number] for number in numbers], "line_numbers": numbers,
            "source": self.corpus["source"]["sec_url"], "passages": passages,
            "document": self.corpus["source"],
        }


def passage_reference(passage, document):
    return {
        "id": passage["id"], "pdf_page": passage["pdf_page"], "section": passage["section"],
        "start_line": passage["start_line"],
        "end_line": passage["start_line"] + len(passage["lines"]) - 1,
        "score": passage["score"], "url": f"{document['pdf_url']}#page={passage['pdf_page']}",
    }
