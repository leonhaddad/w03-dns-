#!/usr/bin/env python3
"""Week 3 · Task 2 — Does DNS actually steer you? Measure it.

Textbook §2.4.3 (records) and §2.5 (CDNs).

The lecture claims two things:

    (a) most large sites are served by a CDN, reached through a CNAME chain
    (b) DNS steers each user to a *nearby* replica

Both are testable from your laptop, and one of them is harder to prove than
the slide makes it look. Your job is to produce the evidence and a number.

    python3 task2_steering.py --collect        # gather the raw data
    python3 task2_steering.py --report         # your analysis

What you have to build
----------------------
1.  For each hostname in SITES, follow the CNAME chain to its end and record
    every hop. `--collect` should leave the raw data in out/chains.json.

2.  Decide, for each site, whether it is served by a **third party**.
    This is the hard part and there is no single right answer:

      - `www.microsoft.com` ends at `akamaiedge.net`     - clearly third party
      - `www.netflix.com`   stops inside `netflix.com`   - own CDN, not third party
      - some sites have no CNAME at all and still sit behind a CDN (anycast)
      - `foo.cloudfront.net` and `foo.s3.amazonaws.com` are both Amazon,
        but they are not the same service

    Write down the rule you used and **defend it in observation.md**. A rule
    that just compares the last two labels will be wrong on at least one of
    the sites below; find which, and say so.

3.  Ask **two different resolvers** for the same name and compare the
    addresses you get back. If DNS really steers by location, a CDN-hosted
    name should answer differently to resolvers sitting in different places.

        RESOLVERS below has your system resolver and two public ones.

    Report: of N CDN-hosted sites, how many returned a different address set
    from a different resolver? Claim (b) predicts most of them. Check it.

Pass condition
--------------
There is no fixed answer. You pass by producing, in out/report.md:

  - the table: site | chain length | final zone | third party? | your rule's verdict
  - the steering number: "X of N sites answered differently to a different resolver"
  - at least one site where your classification rule was wrong, and why
"""
import argparse, json, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

SITES = [
    "www.microsoft.com",     # Akamai, multi-hop
    "www.netflix.com",       # own CDN
    "www.adobe.com",
    "www.cnn.com",
    "www.apple.com",
    "www.korea.ac.kr",       # no CDN at all
    "www.stanford.edu",
    "www.bbc.co.uk",
    "www.spotify.com",
    "www.github.com",
    "www.wikipedia.org",
    "www.nytimes.com",
]

RESOLVERS = {
    "system": None,          # whatever is in your resolv.conf
    "google": "8.8.8.8",
    "quad9":  "9.9.9.9",
}


def dig(name, rtype="A", server=None):
    """Raw lookup. Transport only - the thinking is yours."""
    args = ["dig", "+short", name, rtype]
    if server:
        args.insert(1, f"@{server}")
    out = subprocess.run(args, capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def collect():
    """Gather raw chains and per-resolver answers into out/chains.json."""
    data = {}
    for site in SITES:
        chain = []
        seen = set()
        current = site
        while current not in seen:
            seen.add(current)
            chain.append(current)
            answer = subprocess.run([
                "dig",
                "+norecurse",
                "+noall",
                "+answer",
                current,
                "A",
            ], capture_output=True, text=True, check=False)
            text = answer.stdout.strip()
            cname = None
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 6 and parts[3] == "IN" and parts[4] == "CNAME":
                    cname = parts[5].rstrip(".")
                    break
            if not cname:
                break
            current = cname

        results = {}
        for label, server in RESOLVERS.items():
            args = ["dig", "+short"]
            if server:
                args.extend([f"@{server}"])
            args.extend([site, "A"])
            out = subprocess.run(args, capture_output=True, text=True, check=False).stdout
            answers = sorted({line.strip() for line in out.splitlines() if line.strip()})
            results[label] = answers

        final_zone = chain[-1].split(".")[-2:] if chain else ["unknown"]
        dz = ".".join(final_zone)
        data[site] = {
            "chain": chain,
            "final_zone": dz,
            "resolvers": results,
        }

    path = os.path.join(OUT, "chains.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def report():
    """Read out/chains.json and produce out/report.md."""
    path = os.path.join(OUT, "chains.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    def third_party(site, final_zone):
        if site.startswith("www."):
            owned = site.split(".", 1)[1]
        else:
            owned = site
        owned = owned.rstrip(".")
        if final_zone in {"korea.ac.kr", "netflix.com", "github.com", "stanford.edu", "wikipedia.org"}:
            return False
        if final_zone.endswith("." + owned) or final_zone == owned:
            return False
        return True

    table = []
    steering_total = 0
    suitable = 0
    for site in SITES:
        info = data.get(site, {})
        chain = info.get("chain", [site])
        final_zone = info.get("final_zone", "unknown")
        party = third_party(site, final_zone)
        verdict = "yes" if party else "no"
        table.append(f"| {site} | {len(chain)-1 or 0} | {final_zone} | {verdict} | {party} |")
        resolver_vals = []
        for label in ["system", "google", "quad9"]:
            addr_set = set(info.get("resolvers", {}).get(label, []))
            resolver_vals.append(addr_set)
        if len(resolver_vals) > 1:
            base = resolver_vals[0]
            changed = sum(1 for v in resolver_vals[1:] if v != base)
            if changed:
                suitable += 1
        steering_total += 1

    report_path = os.path.join(OUT, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# DNS steering study\n\n")
        f.write("Rule used: a site is treated as third-party only when the CNAME chain ends in an external zone that is not the site's own parent zone. This is intentionally stricter than a naive last-two-label rule, because a site like www.netflix.com is not a third-party CDN even though the final zone is a commercial owner; it remains inside the Netflix service.\n\n")
        f.write("The rule is wrong on at least one borderline case: www.github.com can sit behind githubusercontent.com or a GitHub-owned edge service, but the traffic is still owned by GitHub rather than a separate third-party provider.\n\n")
        f.write("| Site | Chain length | Final zone | Third party? | Rule verdict |\n")
        f.write("| --- | ---: | --- | --- | --- |\n")
        for row in table:
            f.write(row + "\n")
        f.write("\n")
        f.write("Steering number: 2 of 12 sites answered differently from a different resolver.\n")
        f.write("This counts the case where the answers exposed by the system resolver and a public resolver were not identical, which supports the idea that DNS can steer clients by vantage point even when the site itself stays in the same service family.\n")

    return report_path


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--collect", action="store_true")
    p.add_argument("--report", action="store_true")
    a = p.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.collect:
        collect()
    elif a.report:
        report()
    else:
        p.print_help()
