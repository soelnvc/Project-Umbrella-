"""Devanagari to Latin, for comparing names across scripts (मोहम्मद शाहिद ~ Mohammad Shahid).

A small table of our own: consonants carry an inherent "a" unless a vowel sign or halant
follows; the final inherent "a" of a word is dropped; anusvara becomes n or m by the next
consonant. The result only has to compare well after phonetic folding, not read perfectly.
"""
from __future__ import annotations

CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh",
    "फ़": "f", "य़": "y",
}
NUKTA_FORMS = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f", "य": "y"}
VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
          "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
SIGNS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
         "ौ": "au", "ॉ": "o", "ॅ": "e"}
HALANT = "्"
NUKTA = "़"
ANUSVARA = "ं"
CANDRABINDU = "ँ"
VISARGA = "ः"
_LABIALS = set("पफबभम")


def transliterate(word: str) -> str:
    out: list[str] = []
    i, n = 0, len(word)
    while i < n:
        ch = word[i]
        if ch in CONSONANTS:
            sound = CONSONANTS[ch]
            j = i + 1
            if j < n and word[j] == NUKTA:
                sound = NUKTA_FORMS.get(ch, sound)
                j += 1
            out.append(sound)
            if j < n and word[j] in SIGNS:
                out.append(SIGNS[word[j]])
                j += 1
            elif j < n and word[j] == HALANT:
                j += 1
            elif j < n and (word[j] in CONSONANTS or word[j] in (ANUSVARA, CANDRABINDU)):
                out.append("a")
            i = j
            continue
        if ch in VOWELS:
            out.append(VOWELS[ch])
        elif ch in (ANUSVARA, CANDRABINDU):
            nxt = word[i + 1] if i + 1 < n else ""
            out.append("m" if nxt in _LABIALS else "n")
        elif ch == VISARGA:
            out.append("h")
        elif ch in SIGNS:
            out.append(SIGNS[ch])
        elif ch not in (HALANT, NUKTA):
            out.append(ch)
        i += 1
    return "".join(out)


def to_latin(text: str) -> str:
    return " ".join(transliterate(w) if any("\u0900" <= c <= "\u097F" for c in w) else w for w in text.split())
