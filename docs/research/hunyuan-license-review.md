# Tencent Hunyuan3D License Review

Scope: is the Hunyuan3D model family safe to adopt for this user's situation — solo
US-based developer; personal projects; indie games shipped commercially on Steam
and mobile; 3D-printable part designs; models run locally; outputs (meshes/textures)
ship inside the products.

All license texts below were fetched directly from the official GitHub raw file
paths on 2026-09-24 (not secondhand summaries, not the HTML-rendered GitHub page).
Every clause quoted is a verbatim excerpt of the fetched text.

## 1. Exact license identity per model

| Model | License name | Release date | Source URL fetched |
|---|---|---|---|
| Hunyuan3D-2.0 (incl. the mv/mv-Fast/mv-Turbo weights, which are distributed from the same repo) | **Tencent Hunyuan 3D 2.0 Community License Agreement** | January 21, 2025 | `https://raw.githubusercontent.com/Tencent-Hunyuan/Hunyuan3D-2/main/LICENSE` |
| Hunyuan3D-2.1 | **Tencent Hunyuan 3D 2.1 Community License Agreement** | June 13, 2025 | `https://raw.githubusercontent.com/Tencent-Hunyuan/Hunyuan3D-2.1/main/LICENSE` |
| Hunyuan3D-Omni | **Tencent Hunyuan 3D Omni Community License Agreement** | September 26, 2025 | `https://raw.githubusercontent.com/Tencent-Hunyuan/Hunyuan3D-Omni/main/License.txt` (note: this repo names the file `License.txt`, not `LICENSE`) |

Confirmation that **Hunyuan3D-2mv is not separately licensed**: the Hugging Face
model card for `tencent/Hunyuan3D-2mv` carries `license_link:
https://huggingface.co/tencent/Hunyuan3D-2/blob/main/LICENSE.txt`, i.e. it points
straight at the Hunyuan3D-2.0 license. There is no independent "2mv" license text.

I diffed the Hunyuan3D-Omni license against the 2.1 license line-by-line: they are
**the same template** with only the product name, release date, and repo URL
substituted (`diff` output confirmed identical Sections 1–9 and identical Exhibit A
wording, just s/Tencent Hunyuan 3D 2.1/Tencent Hunyuan 3D Omni/g and a new date).
The 2.0 license is likewise the same template one version earlier (2.0 lacks one
extra sub-item in Section 4 that 2.1/Omni added: "c. Your plans to modify Tencent
Hunyuan 3D X or create Model Derivatives" as part of the commercial-license request
form — a drafting difference, not a substantive one).

**Bottom line: all four are the identical restrictive "Community License" template.**
This is not an open-source license (not OSI-approved, not MIT/Apache-class); it is a
Meta/Llama-style gated-commercial-use custom license.

## 2. The load-bearing clauses, quoted verbatim

All quotes below are from the fetched Hunyuan3D-2.1 text unless noted; the 2.0 and
Omni texts are word-for-word identical except for the product name substitution
confirmed above.

### 2a. Territory exclusion (EU / UK / South Korea)

From the header, before Section 1:

> "THIS LICENSE AGREEMENT DOES NOT APPLY IN THE EUROPEAN UNION, UNITED KINGDOM AND SOUTH KOREA AND IS EXPRESSLY LIMITED TO THE TERRITORY, AS DEFINED BELOW."

Definition, Section 1(l):

> "'Territory' shall mean the worldwide territory, excluding the territory of the European Union, United Kingdom and South Korea."

Grant of rights, Section 2:

> "We grant You, for the Territory only, a non-exclusive, non-transferable and royalty-free limited license under Tencent's intellectual property or other rights owned by Us embodied in or utilized by the Materials to use, reproduce, distribute, create derivative works of (including Model Derivatives), and make modifications to the Materials, only in accordance with the terms of this Agreement and the Acceptable Use Policy..."

Critically, the territory restriction is **not limited to the model weights** — it
explicitly extends to the generated **Output**. Section 5(c):

> "You must not use, reproduce, modify, distribute, or display the Tencent Hunyuan 3D 2.1 Works, Output or results of the Tencent Hunyuan 3D 2.1 Works outside the Territory. Any such use outside the Territory is unlicensed and unauthorized under this Agreement."

And Exhibit A (Acceptable Use Policy) item 1 restates this as a top-level prohibited
use of the model or its Model Derivatives:

> "You agree not to use Tencent Hunyuan 3D 2.1 or Model Derivatives: 1. Outside the Territory;"

**This is the single most consequential clause for this user's Steam/mobile line.**
Steam and mobile app stores sell/distribute globally by default, including into the
EU, UK, and South Korea. Distributing a game containing a Hunyuan-generated mesh or
texture to a customer in any of those three jurisdictions is "distribut[ing]... the
[...] Output... outside the Territory," which the license itself calls "unlicensed
and unauthorized."

### 2b. MAU threshold

Section 4, "ADDITIONAL COMMERCIAL TERMS":

> "If, on the Tencent Hunyuan 3D 2.1 version release date, the monthly active users of all products or services made available by or for Licensee is greater than 1 million monthly active users in the preceding calendar month, You must request a license from Tencent, which Tencent may grant to You in its sole discretion, and You are not authorized to exercise any of the rights under this Agreement unless or until Tencent otherwise expressly grants You such rights."

For a solo indie developer this threshold is not a near-term practical blocker (1M
MAU is an aspirational problem), but it is worth flagging as a growth ceiling: if an
indie title were to break out, the license lapses automatically and retroactively
requires Tencent's discretionary written approval — there is no guarantee of being
granted a commercial license at all.

### 2c. Use-case restrictions

Section 5(b) (training-competitor clause):

> "You must not use the Tencent Hunyuan 3D 2.1 Works or any Output or results of the Tencent Hunyuan 3D 2.1 Works to improve any other AI model (other than Tencent Hunyuan 3D 2.1 or Model Derivatives thereof)."

Exhibit A, Acceptable Use Policy (full list — items relevant to a shipped commercial
game or printable-part product are bolded in my annotation, text itself is verbatim):

> "Tencent endeavors to promote safe and fair use of its tools and features, including Tencent Hunyuan 3D 2.1. You agree not to use Tencent Hunyuan 3D 2.1 or Model Derivatives:
> 1. Outside the Territory;
> 2. In any way that violates any applicable national, federal, state, local, international or any other law or regulation;
> 3. To harm Yourself or others;
> 4. To repurpose or distribute output from Tencent Hunyuan 3D 2.1 or any Model Derivatives to harm Yourself or others;
> 5. To override or circumvent the safety guardrails and safeguards We have put in place;
> 6. For the purpose of exploiting, harming or attempting to exploit or harm minors in any way;
> 7. To generate or disseminate verifiably false information and/or content with the purpose of harming others or influencing elections;
> 8. To generate or facilitate false online engagement, including fake reviews and other means of fake online engagement;
> 9. To intentionally defame, disparage or otherwise harass others;
> 10. To generate and/or disseminate malware (including ransomware) or any other content to be used for the purpose of harming electronic systems;
> 11. To generate or disseminate personal identifiable information with the purpose of harming others;
> 12. To generate or disseminate information (including images, code, posts, articles), and place the information in any public context (including –through the use of bot generated tweets), without expressly and conspicuously identifying that the information and/or content is machine generated;
> 13. To impersonate another individual without consent, authorization, or legal right;
> 14. To make high-stakes automated decisions in domains that affect an individual's safety, rights or wellbeing (e.g., law enforcement, migration, medicine/health, management of critical infrastructure, safety components of products, essential services, credit, employment, housing, education, social scoring, or insurance);
> 15. In a manner that violates or disrespects the social ethics and moral standards of other countries or regions;
> 16. To perform, facilitate, threaten, incite, plan, promote or encourage violent extremism or terrorism;
> 17. For any use intended to discriminate against or harm individuals or groups based on protected characteristics or categories, online or offline social behavior or known or predicted personal or personality characteristics;
> 18. To intentionally exploit any of the vulnerabilities of a specific group of persons based on their age, social, physical or mental characteristics, in order to materially distort the behavior of a person pertaining to that group in a manner that causes or is likely to cause that person or another person physical or psychological harm;
> 19. For military purposes;
> 20. To engage in the unauthorized or unlicensed practice of any profession including, but not limited to, financial, legal, medical/health, or other professional practices."

Item 12 is the one worth flagging for game shipping: placing generated content "in
any public context" (a published Steam/mobile game qualifies) without "expressly
and conspicuously identifying that the [...] content is machine generated" is a
prohibited use. This is a stricter, contractually-enforceable version of a platform
AI-disclosure checkbox — it is a license condition, not just a storefront form
field.

### 2d. Output ownership

Section 6(d):

> "Tencent claims no rights in Outputs You generate. You and Your users are solely responsible for Outputs and their subsequent uses."

Read in isolation this looks like a clean "you own your outputs" grant. It is not
unconditional: Section 5(c) and AUP item 1 (quoted above in 2a/2c) explicitly extend
the Territory ban and the AUP to "Output," so "no rights claimed" only means Tencent
is not asserting IP ownership over the mesh/texture itself — the contractual use
restrictions on *how and where* that Output may be used/distributed still apply, and
they are the more commercially important restriction for this user.

### 2e. Distribution / attribution / Notice requirements

Section 3:

> "You may, subject to Your compliance with this Agreement, distribute or make available to Third Parties the Tencent Hunyuan 3D 2.1 Works, exclusively in the Territory, provided that You meet all of the following conditions:
> a. You must provide all such Third Party recipients of the Tencent Hunyuan 3D 2.1 Works or products or services using them a copy of this Agreement;
> b. You must cause any modified files to carry prominent notices stating that You changed the files;
> c. You are encouraged to: (i) publish at least one technology introduction blogpost or one public statement expressing Your experience of using the Tencent Hunyuan 3D 2.1 Works; and (ii) mark the products or services developed by using the Tencent Hunyuan 3D 2.1 Works to indicate that the product/service is 'Powered by Tencent Hunyuan'; and
> d. All distributions to Third Parties (other than through a Hosted Service) must be accompanied by a 'Notice' text file that contains the following notice: 'Tencent Hunyuan 3D 2.1 is licensed under the Tencent Hunyuan 3D 2.1 Community License Agreement, Copyright © 2025 Tencent. All Rights Reserved. The trademark rights of "Tencent Hunyuan" are owned by Tencent or its affiliate.'"

Note (c) is only "encouraged," not mandatory; (a), (b), (d) are mandatory *only when
distributing the model Works themselves* (weights/code) to a third party — Section
3's chain says "If You receive Tencent Hunyuan 3D 2.1 Works from a Licensee as part
of an integrated end user product, then this Section 3 of this Agreement will not
apply to You," which suggests shipping only *generated Output* baked into a game
(not the model weights themselves) does not trigger the Notice-file requirement.
However, Section 3(e) is broader and does bite on Output-bearing products:

> "In the event that You use, integrate, implement, or otherwise deploy the Tencent Hunyuan Works, in whole or in part, to provide, enable, or support any service, product, or functionality to third parties, You shall clearly, accurately, and prominently disclose to all end users the full legal name and entity of the actual provider of such service, product, or functionality. You shall expressly and conspicuously state that Tencent is not affiliated with, associated with, sponsoring, or endorsing any such service, product, or functionality."

This means shipping a game whose assets were generated with Hunyuan3D would require
a prominent in-product disclosure of the developer's legal identity plus a
non-affiliation statement about Tencent — a real UX/legal-page burden for a solo
indie shipping on Steam/mobile.

### 2e-bis. Litigation/patent termination (IP-defensive clause)

Section 6(c):

> "If You commence a lawsuit or other proceedings (including a cross-claim or counterclaim in a lawsuit) against Us or any person or entity alleging that the Materials or any Output, or any portion of any of the foregoing, infringe any intellectual property or other right owned or licensable by You, then all licenses granted to You under this Agreement shall terminate as of the date such lawsuit or other proceeding is filed. You will defend, indemnify and hold harmless Us from and against any claim by any Third Party arising out of or related to Your or the Third Party's use or distribution of the Tencent Hunyuan 3D 2.1 Works."

Note the indemnification obligation runs the other way too: the Licensee (this
user) must "defend, indemnify and hold harmless" Tencent against third-party claims
arising from the user's use/distribution of the Works — a real liability exposure
for a solo developer with no legal department, uncapped by the Agreement.

### 2f. Acceptable Use Policy can change unilaterally ("hook that could change later")

Exhibit A header:

> "Tencent reserves the right to update this Acceptable Use Policy from time to time. Last modified: November 5, 2024."

The Agreement incorporates the AUP "by reference" (Section 5(a)), meaning Tencent
can tighten or add restrictions after this user has already shipped a product built
on the model, and the user's existing distributions become retroactively subject to
whatever the AUP says at the time of use. There is no version-pinning or grandfather
clause in the text.

### 2g. No warranty, broad disclaimer, governing law

Section 7(b)/(c) — standard "AS IS" disclaimer and liability cap language (quoted
for completeness, not itself a blocker beyond ordinary open-source norms):

> "THE TENCENT HUNYUAN 3D 2.1 WORKS AND ANY OUTPUT AND RESULTS THEREFROM ARE PROVIDED 'AS IS' WITHOUT ANY EXPRESS OR IMPLIED WARRANTIES OF ANY KIND..."

Section 9(a): governing law is "the laws of the Hong Kong Special Administrative
Region of the People's Republic of China," with exclusive jurisdiction in a Hong Kong
court (Section 9(b)) — meaningful if this user ever needed to dispute a licensing
decision; a US solo developer has no practical recourse there.

## 3. Do Outputs carry restrictions distinct from the model weights?

**Yes, and this is the crux of the analysis.** Section 6(d) disclaims Tencent's *IP*
ownership over generated Outputs (meshes/textures) — Tencent is not claiming
copyright in what you generate. But the Agreement's *use restrictions* explicitly
follow the Output, not just the weights:

- Territory: Section 5(c) bans using/distributing/displaying "Output or results of
  the [...] Works outside the Territory" — i.e., a mesh generated locally on this
  user's US machine still cannot be shipped to an EU/UK/South Korean customer.
- AUP item 1 restates the same Territory ban directly against Output usage.
- Section 5(b) bans using Output to train/improve other AI models.
- Section 3(e) requires an affirmative non-affiliation disclosure to end users of
  any product built using the Works "in whole or in part" — this reads as covering
  Output-only integration, not just weight redistribution.
- AUP item 12 requires conspicuous "machine generated" labeling of Output placed in
  any public context.

So: **weights are not for sale/redistribution outside narrow conditions, and Output
is "yours" in the copyright sense but still shackled by the same Territory,
AI-disclosure, and non-affiliation-disclosure obligations that bind the model
itself.** This is unusual compared to typical "outputs are unrestricted" model
licenses (e.g., Stability's or OpenAI's output clauses) and is the main reason this
license is materially worse than it first appears from the "Tencent claims no
rights in Outputs" headline sentence alone.

## 4. Per-model verdict for this user's two product lines

All four models carry the identical license terms, so the verdict is identical
across Hunyuan3D-2.0, Hunyuan3D-2mv, Hunyuan3D-2.1, and Hunyuan3D-Omni. I give one
verdict per product line rather than per model, since the model choice doesn't
change the legal analysis.

**Personal projects (no distribution to third parties):**
**SAFE-WITH-CONDITIONS.** Running the model locally, generating meshes, and using
them for the user's own non-distributed purposes does not trigger the Territory,
Notice, or AUP-disclosure clauses in any way that matters — there's no "Third
Party," no public product. Condition: don't post the raw generated assets publicly
without machine-generated labeling (AUP item 12 technically covers "any public
context," which could include a portfolio post or personal blog), and don't use the
outputs to train another model.

**Indie games shipped commercially on Steam + mobile:**
**UNSAFE as a practical matter**, functionally equivalent to unusable for this
product line without an operationally painful workaround. The reasons, in order of
severity:
1. Steam and mobile storefronts distribute worldwide by default; excluding the EU,
   UK, and South Korea (three large, high-ARPU game markets) via geo-blocking is the
   *only* way to stay inside Section 5(c)/AUP item 1's Territory restriction, and
   that is a real revenue and store-configuration cost for a solo developer for
   every single title, forever, as long as any Hunyuan-derived asset ships in it.
2. Section 3(e) requires a prominent in-product/legal disclosure of the developer's
   identity and Tencent's non-affiliation — an unusual, license-mandated UX
   requirement most other model licenses don't impose.
3. AUP item 12 requires conspicuously labeling any publicly placed generated content
   as machine-generated — this could force visible "AI-generated asset" labeling
   inside a shipped game, which is a design/marketing constraint most competing
   MIT/Apache tools don't impose.
4. Section 6(c)'s litigation-termination + mandatory indemnification clause is a
   real, uncapped liability exposure for a one-person studio with no legal team.
5. The AUP "can be updated by Tencent from time to time" and is incorporated by
   reference — a shipped, unpatchable game could become retroactively
   non-compliant with no user recourse.
If this user is willing to geo-block the EU/UK/South Korea on every title and add
the disclosure language, this becomes SAFE-WITH-CONDITIONS instead of UNSAFE — but
given the project's own docs already treat MIT/Apache as a hard requirement (see
Section 5), the practical verdict for this pipeline is **UNSAFE / do not adopt**.

**3D-printable part designs:**
Same analysis as above, gated by *how* the designs are distributed:
- If the user only prints parts for personal use and never sells/distributes the
  STL/mesh files or physical prints to customers, this is **SAFE-WITH-CONDITIONS**
  (same conditions as "personal projects" above).
- If the user sells/ships printed parts or design files to customers (e.g., Etsy,
  Printables, a webstore, or even a US-only store with international shipping),
  **UNSAFE** unless sales/shipping is contractually restricted from the EU, UK, and
  South Korea — again a real commercial constraint for a marketplace listing that
  by default ships internationally.

## 5. Comparison to the MIT/Apache alternatives already adopted in this project

`docs/env-registry.md` (line 21) and `docs/research/3d-generation-and-detailing.md`
(the model-comparison table, lines 101–121) show this project already made this
same call and ruled Hunyuan3D out on exactly these grounds: "Models must be
MIT/Apache-class. Hunyuan's licence was rejected on purpose: EU/UK/KR exclusions,
MAU cap, mandatory AI-content labelling." The models actually in use — TRELLIS.2
(MIT), Pixal3D (MIT), and the queued/candidate TripoSG, Direct3D-S2, Hi3DGen, and
PartCrafter (all MIT) — impose none of the Hunyuan license's conditions: no
territory carve-outs, no MAU cap forcing a renegotiation on growth, no AI-disclosure
mandate baked into the license, no license-mandated non-affiliation disclosure UX,
no litigation-termination/indemnification clause reaching into the user's own IP
disputes, and no incorporated-by-reference policy Tencent can silently tighten
later. Under MIT/Apache, the generated Output and the pipeline that produced it can
ship to any Steam/mobile customer worldwide, be sold as part of a printed product
internationally, and be modified/redistributed with only the minimal MIT/Apache
notice-preservation requirement — a categorically lower compliance burden than the
Hunyuan Community License in every dimension examined above. This project's existing
policy of treating Hunyuan as excluded (per `docs/research/3d-generation-and-detailing.md`
lines 110-111, 127-128, 442) is consistent with what this license review found
independently from the primary source texts.

## Sources fetched directly (not secondhand)

- `https://raw.githubusercontent.com/Tencent-Hunyuan/Hunyuan3D-2/main/LICENSE` (full text retrieved and quoted above)
- `https://raw.githubusercontent.com/Tencent-Hunyuan/Hunyuan3D-2.1/main/LICENSE` (full text retrieved and quoted above)
- `https://raw.githubusercontent.com/Tencent-Hunyuan/Hunyuan3D-Omni/main/License.txt` (full text retrieved; diffed against the 2.1 text to confirm identical terms)
- `https://huggingface.co/tencent/Hunyuan3D-2mv/raw/main/README.md` (model-card front matter confirming 2mv shares the Hunyuan3D-2.0 license, no independent license text exists for 2mv)
- `https://huggingface.co/tencent/Hunyuan3D-Omni/raw/main/README.md` (model-card front matter, confirms `license_name: tencent-hunyuan-community`, `extra_gated_eu_disallowed: true`)

No license text in this report was taken from a secondary summary — the raw files
above were downloaded and read directly, and every clause quoted is a verbatim
excerpt.
