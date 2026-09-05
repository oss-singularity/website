# Reproducible robots policy

`site/robots.txt` preserves the exact policy already served publicly by Cloudflare
for `https://oss-singularity.io/robots.txt` on 2026-09-05. This change moves that
existing response into version control. It introduces no new permissions,
restrictions, crawler groups, or training policy.

## Captured baseline

- Existing Origin and release `d883ebe174c989d4a278621f86086a9f78bf44bf`:
  72 bytes, SHA-256
  `9f43f4a2c9a9d557c046fd79186287669e22a874344d51e09f6929ce10d480bd`.
- Existing public Edge response, confirmed by two consecutive reads before the
  source update: 1,908 bytes, SHA-256
  `c96b21c43640ba554286ca54f225edbb2eadcaae9432a813d9955af42fb76993`.
- Cloudflare prepended 1,836 bytes. The original 72-byte file, including its
  `User-agent: *`, `Allow: /`, sitemap URL, and final newline, remains the exact
  suffix of the versioned file.

The preserved response declares
`Content-Signal: search=yes,ai-train=no,use=reference`. It has no explicit
`ai-input` value. Its named `Disallow: /` groups are Amazonbot,
Applebot-Extended, Bytespider, CCBot, ClaudeBot,
CloudflareBrowserRenderingCrawler, Google-Extended, GPTBot, and
meta-externalagent. The Content Signal definitions and Cloudflare marker
comments are retained verbatim as part of the captured response.

An invitation to participate in the Commons does not change these existing
content-use preferences. Any future policy change needs a separate, explicit
decision and review; this migration does not make that decision.

## Cutover contract

1. Review and release this source file through the normal site workflow. Keep
   the old release and the current Cloudflare configuration available for
   rollback. Do not rewrite an already verified release directory.
2. Verify the new Origin response against the built 1,908-byte artifact before
   disabling automatic augmentation. Cloudflare may temporarily prepend its
   managed block again while the new Origin and old setting coexist.
3. Disable only the automatic managed-robots augmentation for the OSS
   Singularity zone (`is_robots_txt_managed: false`). Preserve all other existing
   AI crawler, Bot Management, WAF, and content-policy settings. Compare the
   relevant configuration before and after the update. If another setting still
   transforms the response, investigate it rather than resetting unrelated
   controls.
4. Verify that the public Edge, Origin, and built artifact are byte-identical,
   with the captured Edge hash above. Retain the existing exact-byte verifier;
   do not strip comments or permit arbitrary provider prefixes to make it pass.
5. On failure, restore the recorded configuration and prior release together.
   Subsequent robots policy updates are source-reviewed changes rather than
   automatic additions from Cloudflare's managed list.

Creating these source files does not itself change Cloudflare settings or
perform this cutover. The final public policy must remain the captured policy
throughout the migration; existing crawler enforcement rules remain separate.

## Primary references

- [Cloudflare managed robots.txt behavior and Content Signals](https://developers.cloudflare.com/bots/additional-configurations/managed-robots-txt/)
- [Cloudflare Bot Management configuration fields](https://developers.cloudflare.com/api/resources/bot_management/methods/update/)
