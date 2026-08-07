"""Parse the shared LOCAL/TONE3000 search-box language."""

from dataclasses import dataclass
import shlex


@dataclass(frozen=True)
class SearchSpec:
    """The normalized search intent passed to a local or remote adapter."""

    text: str = ""
    authors: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    makes: tuple[str, ...] = ()
    model_ids: tuple[int, ...] = ()

class SearchSyntaxError(ValueError):
    """An input error that should be shown without issuing a search request."""


_FIELDS = {
    "author": "authors",
    "tag": "tags",
    "make": "makes",
}


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def parse_search(query: str) -> SearchSpec:
    """Parse bare words plus @author, #tag, author:, tag:, and make: filters.

    ``shlex`` gives field values such as ``tag:"edge of breakup"`` one token
    while retaining ordinary whitespace-separated search terms. Unknown
    ``key:value`` tokens stay in the full-text query so model names containing
    colons remain searchable.
    """
    try:
        tokens = shlex.split(query or "", comments=False, posix=True)
    except ValueError as exc:
        raise SearchSyntaxError("unclosed quote") from exc

    # A standalone number is overwhelmingly an ID pasted from the model
    # detail view. Keep numbers in multi-word queries as ordinary text so a
    # title such as "Fender 1977" still performs a title search.
    if len(tokens) == 1 and tokens[0].isdigit() and int(tokens[0]) > 0:
        return SearchSpec(model_ids=(int(tokens[0]),))

    words: list[str] = []
    authors: list[str] = []
    tags: list[str] = []
    makes: list[str] = []
    model_ids: list[int] = []
    target_lists = {"authors": authors, "tags": tags, "makes": makes}

    for token in tokens:
        if token.startswith("@"):
            value = token[1:]
            if not value:
                raise SearchSyntaxError("@author needs a username")
            _append_unique(authors, value)
            continue
        if token.startswith("#"):
            value = token[1:]
            if not value:
                raise SearchSyntaxError("#tag needs a tag name")
            _append_unique(tags, value)
            continue

        if ":" in token:
            field, value = token.split(":", 1)
            if field.casefold() in ("model", "model_id"):
                if not value:
                    raise SearchSyntaxError(f"{field}: needs a numeric model ID")
                try:
                    model_id = int(value)
                except ValueError as exc:
                    raise SearchSyntaxError(f"{field}: needs a numeric model ID") from exc
                if model_id <= 0:
                    raise SearchSyntaxError(f"{field}: needs a positive model ID")
                _append_unique(model_ids, model_id)
                continue
            target = _FIELDS.get(field.casefold())
            if target is not None:
                if not value:
                    raise SearchSyntaxError(f"{field}: needs a value")
                _append_unique(target_lists[target], value)
                continue

        words.append(token)

    return SearchSpec(
        text=" ".join(words),
        authors=tuple(authors),
        tags=tuple(tags),
        makes=tuple(makes),
        model_ids=tuple(model_ids),
    )
