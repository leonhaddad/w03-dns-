#!/usr/bin/env python3
"""Week 3 · Task 1 — Build your own iterative resolver.

Textbook §2.4.2 - §2.4.3.

`dig +trace` walks root -> TLD -> authoritative for you. In this task you do
that walk yourself: start at a root server, read the delegation it returns,
ask the next server, and keep going until somebody answers authoritatively.

You may shell out to `dig` for the transport, or use a DNS library
(`dnspython` is in the container). Either is fine - what matters is that
*you* follow the delegations rather than letting a tool do it.

    python3 task1_resolve.py www.korea.ac.kr
    python3 task1_resolve.py --verify        # check yourself against dig

Pass condition
--------------
`--verify` resolves five names with your resolver and with `dig`, and the
addresses must agree. A name behind a CDN may legitimately return a different
address each time; the harness compares the *set of authoritative nameservers*
you ended at for those, not the address.
"""
import argparse, subprocess, sys

# Root servers. Everything starts here; there is no earlier step.
ROOT_SERVERS = [
    "198.41.0.4",       # a.root-servers.net
    "199.9.14.201",     # b.root-servers.net
    "192.33.4.12",      # c.root-servers.net
]

# (name, kind).  "stable" names must match dig exactly.  "cdn" names are served
# from many replicas and may legitimately give you a different address than dig
# got a second earlier - for those we only require that you reached an answer.
VERIFY_NAMES = [
    ("www.korea.ac.kr", "stable"),
    ("dns.google", "stable"),
    ("en.wikipedia.org", "stable"),
    ("www.stanford.edu", "stable"),
    ("www.microsoft.com", "cdn"),
]


class Resolver:
    """A small iterative resolver built on top of dig.

    It asks a server for the next delegation, follows NS names to their
    addresses, and keeps going until it reaches an authoritative A answer.
    """

    def __init__(self):
        self.path = []

    def _query(self, name, server):
        args = [
            "dig",
            f"@{server}",
            "+norecurse",
            "+time=2",
            "+tries=1",
            "+noall",
            "+answer",
            "+authority",
            "+additional",
            name,
            "A",
        ]
        out = subprocess.run(args, capture_output=True, text=True)
        text = out.stdout.strip()
        if not text:
            return {"status": "NOANSWER", "answers": [], "authority": [], "additional": []}

        status = "NOERROR"
        match = __import__("re").search(r"status:\s*(\S+)", text, __import__("re").IGNORECASE)
        if match:
            status = match.group(1).upper()

        answers, authority, additional = [], [], []
        section = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith(";; ANSWER SECTION:"):
                section = "answer"
                continue
            if line.startswith(";; AUTHORITY SECTION:"):
                section = "authority"
                continue
            if line.startswith(";; ADDITIONAL SECTION:"):
                section = "additional"
                continue
            if not line or line.startswith(";;") or line.startswith(";"):
                continue
            m = __import__("re").match(r"^(\S+)\s+\d+\s+IN\s+(\S+)\s+(.*)$", line)
            if not m:
                continue
            name_r, rtype, data = m.groups()
            record = {"name": name_r.rstrip("."), "type": rtype.upper(), "data": data.strip()}
            if section == "answer":
                answers.append(record)
            elif section == "authority":
                authority.append(record)
            elif section == "additional":
                additional.append(record)
            elif section is None:
                if rtype.upper() == "NS":
                    authority.append(record)
                elif rtype.upper() in {"A", "AAAA", "CNAME"} and record["name"].lower() == name.lower():
                    answers.append(record)
                elif rtype.upper() in {"A", "AAAA"}:
                    additional.append(record)

        return {
            "status": status,
            "answers": answers,
            "authority": authority,
            "additional": additional,
        }

    def _addresses_from_answer(self, answer_records):
        return [r["data"] for r in answer_records if r["type"] == "A"]

    def _cname_from_answer(self, answer_records):
        c = [r["data"].rstrip(".") for r in answer_records if r["type"] == "CNAME"]
        return c[0] if c else None

    def _delegation(self, authority, additional):
        ns_names = [r["data"].rstrip(".") for r in authority if r["type"] == "NS"]
        glue = {}
        for r in additional:
            if r["type"] == "A":
                glue[r["name"].rstrip(".")] = r["data"]
        return ns_names, glue

    def _resolve_name(self, name, seen, depth):
        name = name.rstrip(".")
        if depth > 20:
            raise RuntimeError(f"loop detected resolving {name!r}")
        if name in seen:
            raise RuntimeError(f"loop detected resolving {name!r}")
        seen = set(seen)
        seen.add(name)

        candidates = list(ROOT_SERVERS)
        tried = set()
        while candidates:
            next_candidates = []
            for server in candidates:
                if server in tried:
                    continue
                tried.add(server)
                self.path.append(server)
                result = self._query(name, server)
                if result["status"] in {"REFUSED", "SERVFAIL"}:
                    continue

                answers = result["answers"]
                addrs = self._addresses_from_answer(answers)
                if addrs:
                    return addrs[0], list(self.path)

                cname = self._cname_from_answer(answers)
                if cname:
                    return self._resolve_name(cname, seen, depth + 1)

                ns_names, glue = self._delegation(result["authority"], result["additional"])
                if not ns_names:
                    continue

                for ns_name in ns_names:
                    if ns_name in glue:
                        ip = glue[ns_name]
                        if ip not in tried and ip not in next_candidates:
                            next_candidates.append(ip)
                        continue
                    try:
                        ip, _ = self._resolve_name(ns_name, seen, depth + 1)
                        if ip not in tried and ip not in next_candidates:
                            next_candidates.append(ip)
                    except Exception:
                        pass

            if not next_candidates:
                break
            candidates = next_candidates
        raise RuntimeError(f"could not resolve {name!r}")

    def resolve(self, name):
        self.path = []
        return self._resolve_name(name, set(), 0)


# ------------------------------------------------------------------- harness
def dig_answer(name):
    """What the system resolver says, for comparison."""
    out = subprocess.run(["dig", "+short", name, "A"],
                         capture_output=True, text=True).stdout
    return [l for l in out.split() if l and l[0].isdigit()]


def verify():
    r, failures = Resolver(), 0
    for name, kind in VERIFY_NAMES:
        try:
            addr, path = r.resolve(name)
        except NotImplementedError:
            print("Nothing implemented yet - write Resolver.resolve first.")
            return 1
        except Exception as e:
            print(f"  FAIL  {name:<22} your resolver raised {e!r}")
            failures += 1
            continue
        expected = dig_answer(name)
        if addr in expected:
            note = ""
        elif kind == "cdn":
            note = "  <- differs, but this name is CDN-hosted. Explain it."
        else:
            note = "  <- should have matched"
            failures += 1
        print(f"  {'FAIL' if note.endswith('matched') else 'ok  '}  {name:<22} "
              f"you={addr:<16} dig={','.join(expected) or '-'}   "
              f"hops={len(path)}{note}")
    print(f"\n  {len(VERIFY_NAMES) - failures}/{len(VERIFY_NAMES)} ok")
    return 1 if failures else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("name", nargs="?", default="www.korea.ac.kr")
    p.add_argument("--verify", action="store_true")
    a = p.parse_args()

    if a.verify:
        sys.exit(verify())

    addr, path = Resolver().resolve(a.name)
    for i, server in enumerate(path, 1):
        print(f"  {i}. asked {server}")
    print(f"\n  {a.name} -> {addr}")


if __name__ == "__main__":
    main()
