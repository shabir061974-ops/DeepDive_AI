# Offline content fixtures

`article.html` and `article.pdf` are authored test documents. Their assertions are
synthetic fixture text, not research findings, citations or real source content.
No fixture was downloaded. The PDF has one page and a simple embedded text stream
using the standard Helvetica font. It was generated locally with the pinned
`pypdf` dependency, without timestamps, credentials or external resources.

Encrypted, active-content, multipage and malformed variants are constructed in
memory by the tests from this fixture; none is fetched or executed.
