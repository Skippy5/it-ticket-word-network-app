"""Text-processing pipeline: clean -> tokenize -> synonyms -> lemmatize ->
phrases -> stopword removal.

Produces, per ticket, a list of normalized tokens. The ticket_id is carried
alongside every step — nothing here ever drops or reorders IDs.

Lemmatization strategy (no mandatory model downloads):
  1. spaCy ``en_core_web_sm`` if installed (best quality),
  2. otherwise a corpus-checked rule lemmatizer: plural rules are applied
     unconditionally (safe), while -ed/-ing stripping is only applied when the
     resulting base form actually occurs elsewhere in the corpus — so
     "replaced" -> "replace" (seen in corpus) but a word whose base never
     appears is left intact instead of being mangled.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass, field

from english_stopwords import ENGLISH_STOP_WORDS

import config

# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_RE_TICKET = re.compile(r"\b(?:INC|RITM|CHG|PRB|REQ|TASK)\d+\b", re.I)
_RE_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"   # 2025-01-08 07:41
    r"|\b\d{1,2}[:/]\d{2}(?:[:/]\d{2})?\s*(?:am|pm)?\b", re.I  # 07:41, 1/2/25
)
# words, keeping intra-word hyphens and digits (error 49 -> handled as phrase;
# bare numbers are dropped after tokenization)
_RE_TOKEN = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")


def clean_text(raw) -> str:
    """Lowercase + strip HTML/entities/URLs/emails/timestamps/ticket numbers."""
    if raw is None:
        return ""
    text = str(raw)
    if not text or text.lower() in ("nan", "none", "null"):
        return ""
    text = html.unescape(text)            # &nbsp; &amp; ...
    text = _RE_HTML_TAG.sub(" ", text)    # <div> <br> ...
    text = text.lower()
    text = _RE_URL.sub(" ", text)
    text = _RE_EMAIL.sub(" ", text)
    text = _RE_TICKET.sub(" ", text)
    text = _RE_TIMESTAMP.sub(" ", text)
    text = text.replace("::", " ")        # export artifact in messy data
    return text


def tokenize(text: str) -> list[str]:
    """Word tokens; drops standalone numbers and 1-char fragments."""
    out = []
    for tok in _RE_TOKEN.findall(text):
        if tok.isdigit():
            continue
        if len(tok) < 2:
            continue
        out.append(tok)
    return out


# ---------------------------------------------------------------------------
# Synonym map
# ---------------------------------------------------------------------------

def apply_synonyms(tokens: list[str], synonyms: dict[str, str]) -> list[str]:
    """Token-level replacement; multi-word targets are re-tokenized so phrase
    detection can pick them up (dl -> ["distribution", "list"])."""
    out: list[str] = []
    for tok in tokens:
        repl = synonyms.get(tok)
        if repl is None:
            out.append(tok)
        else:
            out.extend(repl.lower().split())
    return out


# ---------------------------------------------------------------------------
# Lemmatization
# ---------------------------------------------------------------------------

def _try_spacy():
    try:
        import spacy
        return spacy.load("en_core_web_sm", disable=["parser", "ner"])
    except Exception:
        return None


_SPACY = None
_SPACY_TRIED = False


def _get_spacy():
    global _SPACY, _SPACY_TRIED
    if not _SPACY_TRIED:
        _SPACY = _try_spacy()
        _SPACY_TRIED = True
    return _SPACY


_IRREGULAR = {
    "ran": "run", "saw": "see", "stuck": "stuck", "left": "leave",
    "men": "man", "women": "woman", "mice": "mouse", "geese": "goose",
}


def _plural_to_singular(word: str) -> str:
    """Safe, unconditional plural rules."""
    if len(word) <= 3 or word in config.PROTECTED_TERMS:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


class CorpusLemmatizer:
    """Rule lemmatizer that only strips -ed/-ing when the base form is
    attested in the corpus vocabulary (prevents 'replaced' -> 'replac')."""

    def __init__(self, vocabulary: set[str]):
        # vocabulary should already be plural-normalized
        self.vocab = vocabulary

    def lemma(self, word: str) -> str:
        if word in config.PROTECTED_TERMS:
            return word
        if word in _IRREGULAR:
            return _IRREGULAR[word]
        word = _plural_to_singular(word)
        for suffix in ("ed", "ing"):
            if word.endswith(suffix) and len(word) > len(suffix) + 2:
                stem = word[: -len(suffix)]
                candidates = [stem, stem + "e"]
                if len(stem) > 2 and stem[-1] == stem[-2]:   # mapped -> map
                    candidates.append(stem[:-1])
                if word.endswith("ied"):                      # verified -> verify
                    candidates.append(word[:-3] + "y")
                for cand in candidates:
                    if cand in self.vocab:
                        return cand
        return word


def lemmatize_docs(token_docs: list[list[str]]) -> list[list[str]]:
    """Lemmatize every document. Uses spaCy when available, else corpus rules."""
    nlp = _get_spacy()
    if nlp is not None:
        out = []
        for tokens in token_docs:
            doc = nlp(" ".join(tokens))
            out.append([
                t.lemma_.lower() if t.text not in config.PROTECTED_TERMS else t.text
                for t in doc
                if t.lemma_.strip()
            ])
        return out

    # Build attested vocabulary (plural-normalized) for the rule lemmatizer.
    vocab: set[str] = set()
    for tokens in token_docs:
        for tok in tokens:
            vocab.add(_plural_to_singular(tok))
    lem = CorpusLemmatizer(vocab)
    return [[lem.lemma(tok) for tok in tokens] for tokens in token_docs]


# ---------------------------------------------------------------------------
# Phrases
# ---------------------------------------------------------------------------

def _merge_phrases(
    tokens: list[str],
    seeds: dict[tuple[str, ...], str],
    auto_pairs: set[tuple[str, str]],
) -> list[str]:
    """Greedy left-to-right longest-match merge (3-gram seeds, then 2-gram
    seeds, then auto-detected pairs).

    Merging is ADDITIVE: the phrase token is emitted alongside its constituent
    words. This keeps hub terms alive ("outlook" still exists even when most
    mentions are "outlook web app") and keeps drill-in truthful — a ticket
    saying "print queue" genuinely contains the word "print"."""
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        matched = False
        for size in (3, 2):
            if i + size <= n:
                key = tuple(tokens[i:i + size])
                if key in seeds:
                    out.append(seeds[key])
                    out.extend(key)
                    i += size
                    matched = True
                    break
        if not matched and i + 1 < n and (tokens[i], tokens[i + 1]) in auto_pairs:
            out.append(tokens[i] + "_" + tokens[i + 1])
            out.extend((tokens[i], tokens[i + 1]))
            i += 2
            matched = True
        if not matched:
            out.append(tokens[i])
            i += 1
    return out


def detect_phrases(
    token_docs: list[list[str]],
    stopwords: set[str],
    min_count: int = 3,
    threshold: float = 8.0,
) -> set[tuple[str, str]]:
    """gensim-Phrases-style bigram detection:
    score = (count(a,b) - min_count) * N / (count(a) * count(b))  > threshold.
    Only pairs where neither word is a stopword are considered."""
    unigram = Counter()
    bigram = Counter()
    for tokens in token_docs:
        unigram.update(tokens)
        for a, b in zip(tokens, tokens[1:]):
            if a in stopwords or b in stopwords or a == b:
                continue
            bigram[(a, b)] += 1
    n_words = max(sum(unigram.values()), 1)
    accepted = set()
    for (a, b), ab_count in bigram.items():
        if ab_count < min_count:
            continue
        score = (ab_count - min_count) * n_words / (unigram[a] * unigram[b])
        if score > threshold:
            accepted.add((a, b))
    return accepted


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    ticket_ids: list[str]                       # same order as docs
    docs: list[list[str]]                       # final tokens per ticket
    stopwords: set[str] = field(default_factory=set)
    detected_phrases: set[tuple[str, str]] = field(default_factory=set)

    @property
    def n_docs(self) -> int:
        return len(self.docs)


def build_stopword_set(extra_stopwords: list[str] | None = None) -> set[str]:
    sw = set(ENGLISH_STOP_WORDS)
    sw.update(w.lower() for w in config.IT_STOPWORDS)
    if extra_stopwords:
        sw.update(w.strip().lower() for w in extra_stopwords if w.strip())
    return sw


def run_pipeline(
    records: list[dict],
    text_columns: list[str],
    stopwords: set[str] | None = None,
    synonyms: dict[str, str] | None = None,
    phrase_detection: bool = True,
    phrase_min_count: int = 3,
    phrase_threshold: float = 8.0,
    seed_phrases: list[str] | None = None,
) -> PipelineResult:
    """records: list of dicts with at least 'ticket_id' plus the text columns.
    Returns one token-document per ticket, in input order."""
    stopwords = stopwords if stopwords is not None else build_stopword_set()
    synonyms = synonyms if synonyms is not None else dict(config.SYNONYMS)
    seed_list = seed_phrases if seed_phrases is not None else config.SEED_PHRASES

    ticket_ids: list[str] = []
    token_docs: list[list[str]] = []
    for rec in records:
        parts = [clean_text(rec.get(col)) for col in text_columns]
        tokens = tokenize(" ".join(p for p in parts if p))
        tokens = apply_synonyms(tokens, synonyms)
        ticket_ids.append(str(rec["ticket_id"]))
        token_docs.append(tokens)

    token_docs = lemmatize_docs(token_docs)

    # Seed phrases (lemmatize their words through the same plural rules so
    # "fan vents" matches "fan vent" after normalization).
    seeds: dict[tuple[str, ...], str] = {}
    for phrase in seed_list:
        words = tuple(_plural_to_singular(w) for w in phrase.lower().split())
        if len(words) >= 2:
            seeds[words] = "_".join(words)
        raw = tuple(phrase.lower().split())
        if len(raw) >= 2:
            seeds[raw] = "_".join(words)

    detected: set[tuple[str, str]] = set()
    if phrase_detection:
        detected = detect_phrases(
            token_docs, stopwords,
            min_count=phrase_min_count, threshold=phrase_threshold,
        )
    token_docs = [_merge_phrases(toks, seeds, detected) for toks in token_docs]

    # Stopword removal last so phrases built from clean words survive intact.
    final_docs = [
        [t for t in toks if t not in stopwords and len(t) >= 2]
        for toks in token_docs
    ]
    return PipelineResult(
        ticket_ids=ticket_ids,
        docs=final_docs,
        stopwords=stopwords,
        detected_phrases=detected,
    )
