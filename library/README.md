# The library

Six sources the app reads at startup. `index.json` is the shelf: what each one is,
what it is good for, how much it can be trusted, and — as importantly — what it is
**silent** on.

That last field is the point of the whole folder. Two of these books are widely
cited for things they do not contain. Neither Hoos nor Münchow nor Brault mentions
rate of rise, the crash, or the flick anywhere; Münchow does not mention rate of
rise at all. The app makes claims about all three, and now says plainly that they
rest on practitioner convention rather than on any of these texts.

```
library/
  index.json                                     the shelf — read this first
  roasting/
    hoos-2015-modulating-the-flavor-profile.md   Maillard time, development, acidity
    munchow-2018-roasting-foundation.md          colour, repeatability, probes
    brault-the-coffee-roasters-handbook.md       roast levels, green and roast faults
  processing/
    kornman-2024-fermentation-flavor-continuum.md   processing method and the cup
  sensory/
    wcr-sensory-lexicon.md                       how the lexicon works
    wcr-sensory-lexicon-vocabulary.json          109 attributes, 17 categories
  varieties/
    wcr-arabica-and-robusta-varieties.md         how to read the catalogue
    wcr-arabica-and-robusta-varieties.json       117 varieties
```

## What is in a source file

Each roasting and processing file is written to one shape, because the shape is
what makes the sources comparable:

```markdown
### <claim>
- **Claim:**      one sentence
- **Mechanism:**  why the source says it happens
- **Evidence:**   designed experiment (with panel) · cited study (named) ·
                  the author's practice · assertion
- **Numbers:**    temperatures, times, rates, percentages
- **Page:**       n
```

The **Evidence** line is the one that matters. It is the difference between "a
blind panel over four coffees found this" and "a roaster believes this", and the
app grades its own findings from it. A claim with no experiment behind it is not
worthless — it is the best anyone has on most of these questions — but it should
not be quoted at a roaster as though it were measured.

## Rights

Nothing here reproduces anybody's prose. The roasting and processing files are
findings and citations in our own words, which is what a literature review is.

Two sources ship as data, and they are not on the same footing:

**The variety catalogue** is © World Coffee Research under
[CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/), which
permits free sharing for noncommercial use with attribution. WCR's own statement:

> The Arabica + Robusta Varieties Catalog by World Coffee Research are licensed
> under a Creative Commons Attribution-NonCommercial-NoDerivatives 4.0
> International License. This material is freely available for sharing, copying
> and noncommercial distribution. However, you may not alter the catalogs or data
> in any way, and you may not sell the catalog—it must be distributed freely. If
> you share or distribute this material, you must give appropriate credit to
> World Coffee Research.

Note *NoDerivatives*: the JSON here is a reformatting of their data, which is
arguably a derivative. It is included for noncommercial use with credit. **If
Roast Coach is ever sold, this file has to come out or be licensed separately.**

**The Sensory Lexicon** carries no licence at all — its only rights statement is
*"Copyright © 2017 World Coffee Research. All rights reserved."* So this
repository holds the attribute names, their categories and their reference
intensities, which are facts about a measuring instrument, and **not WCR's
definitions**, which are their writing.

The app shows the definitions anyway, from your own copy:

```bash
python3 tools/import_lexicon.py ~/Downloads/wcr-sensory-lexicon.pdf
```

That reads the PDF you downloaded from
[WCR](https://worldcoffeeresearch.org/resources/sensory-lexicon) — free, and they
would rather you had it — and writes the definitions into your own database,
which is private. The definitions then appear throughout the app. Without that
step you get the vocabulary and no definitions, which is still a working tasting
picker.

If you would rather ship the definitions in the repository, that is WCR's
decision to give, not ours to assume. `tools/permission-request.md` is a note you
can send them.
