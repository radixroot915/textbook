import os
import zlib
from config import HASH_PATH, MAX_FINGERPRINTS, MIN_TEXT_LENGTH, SIMHASH_THRESHOLD


class Bouncer:
    def __init__(self, threshold=SIMHASH_THRESHOLD, path=None):
        self.threshold = threshold
        self.path = path or HASH_PATH
        self.fingerprints = []
        self._load_memory()

    def _get_64bit_simhash(self, text):
        v = [0] * 64
        features = [text[i:i+6] for i in range(len(text)-5)]
        for feature in features:
            h = zlib.adler32(feature.encode('utf-8')) & 0xffffffff
            h_64 = (h << 32) | (h ^ 0x55555555)
            for i in range(64):
                v[i] += 1 if (h_64 >> i) & 1 else -1

        fingerprint = 0
        for i in range(64):
            if v[i] > 0: fingerprint |= (1 << i)
        return fingerprint

    def _load_memory(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            self.fingerprints.append(int(line.strip(), 16))
                        except ValueError:
                            continue
        # Keep only the most recent fingerprints to bound memory usage
        if len(self.fingerprints) > MAX_FINGERPRINTS:
            self.fingerprints = self.fingerprints[-MAX_FINGERPRINTS:]

    def is_duplicate(self, text):
        if len(text) < MIN_TEXT_LENGTH:
            return False
        new_fp = self._get_64bit_simhash(text)
        for fp in self.fingerprints:
            if bin(new_fp ^ fp).count('1') <= self.threshold:
                return True
        return False

    def register_fingerprint(self, text):
        if len(text) < MIN_TEXT_LENGTH:
            return
        fp = self._get_64bit_simhash(text)
        self.fingerprints.append(fp)
        if len(self.fingerprints) > MAX_FINGERPRINTS:
            self.fingerprints = self.fingerprints[-MAX_FINGERPRINTS:]

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{fp:016x}\n")
