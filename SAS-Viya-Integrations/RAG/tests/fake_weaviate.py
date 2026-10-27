# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""An in-memory Weaviate for the adapter tests.

It answers the REST and GraphQL requests the adapter and the manifested
retrieval model send - the shapes they emit, not Weaviate's whole API - and
behaves like the server where the adapter depends on it:

* a batch with an existing id REPLACES the object (no partial update);
* a property must be declared before it is written (auto-schema off);
* a filter on an undeclared property is refused, as Weaviate refuses it;
* IsNull needs indexNullState on the collection;
* a query reaches at most QUERY_MAXIMUM_RESULTS objects (offset + limit), and
  a batch delete removes at most `delete_limit` per call.

Distances are Weaviate's: cosine = 1 - cos, l2-squared, dot = -dot.
"""
import datetime
import json
import math
import re


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _gql_to_json(text: str) -> str:
    """Our GraphQL input syntax back to JSON: bare names become strings."""
    out, i = [], 0
    while i < len(text):
        char = text[i]
        if char == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif char.isdigit() or char == "-":
            j = i + 1           # a number, exponent included (1e-05)
            while j < len(text) and (text[j].isdigit() or text[j] in ".eE+-"):
                j += 1
            out.append(text[i:j])
            i = j
        elif char.isalpha() or char == "_":
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            out.append(word if word in ("true", "false", "null") else f'"{word}"')
            i = j
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _closing(text: str, start: int, opening: str, closing: str) -> int:
    depth, i = 0, start
    while i < len(text):
        if text[i] == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced GraphQL")


def _moment(value):
    text = str(value).replace("Z", "+00:00")
    return datetime.datetime.fromisoformat(text)


class FakeWeaviate:
    def __init__(self, version="1.33.2", max_results=10000, delete_limit=10000):
        self.version = version
        self.max_results = max_results
        self.delete_limit = delete_limit
        self.classes = {}       # name -> schema
        self.objects = {}       # name -> {id: {"properties": {}, "vector": []}}
        self.aliases = {}       # alias -> class
        self.calls = []
        self.headers = []

    # -- transport ----------------------------------------------------------
    def request(self, method, url, json=None, headers=None, verify=None,
                timeout=None):
        path = re.sub(r"^https?://[^/]+", "", url)
        self.calls.append((method, url, json))
        self.headers.append(dict(headers or {}))
        return self._route(method, path, json)

    def close(self):
        pass

    def post(self, url, json=None, headers=None, verify=None, timeout=None):
        """requests.post, as the manifested retrieval model calls it."""
        return self.request("POST", url, json=json, headers=headers,
                            verify=verify, timeout=timeout)

    def _route(self, method, path, body):
        if path == "/v1/meta":
            return FakeResponse(200, {"version": self.version})
        if path == "/v1/schema" and method == "GET":
            return FakeResponse(200, {"classes": list(self.classes.values())})
        if path == "/v1/schema" and method == "POST":
            name = body["class"]
            if name in self.classes:
                return FakeResponse(422, {"error": [{"message": "class exists"}]})
            self.classes[name] = json.loads(json.dumps(body))
            self.objects[name] = {}
            return FakeResponse(200, body)
        found = re.match(r"^/v1/schema/(\w+)(/properties)?$", path)
        if found:
            name, props = found.group(1), found.group(2)
            if name not in self.classes:
                return FakeResponse(404, None)
            if props:
                existing = {p["name"] for p in self.classes[name]["properties"]}
                if body["name"] in existing:
                    return FakeResponse(422, {"error": [{"message": "exists"}]})
                self.classes[name]["properties"].append(body)
                return FakeResponse(200, body)
            if method == "DELETE":
                del self.classes[name]
                del self.objects[name]
                return FakeResponse(200, None)
            return FakeResponse(200, self.classes[name])
        if path == "/v1/batch/objects" and method == "POST":
            return FakeResponse(200, [self._store(o) for o in body["objects"]])
        if path == "/v1/batch/objects" and method == "DELETE":
            return self._batch_delete(body)
        if path == "/v1/graphql":
            try:
                return FakeResponse(200, {"data": self._graphql(body["query"])})
            except ValueError as error:
                return FakeResponse(200, {"errors": [{"message": str(error)}]})
        if path == "/v1/aliases" and method == "POST":
            self.aliases[body["alias"]] = body["class"]
            return FakeResponse(200, body)
        found = re.match(r"^/v1/aliases/(\w+)$", path)
        if found:
            alias = found.group(1)
            if alias not in self.aliases:
                return FakeResponse(404, None)
            if method == "PUT":
                self.aliases[alias] = body["class"]
            return FakeResponse(200, {"alias": alias, "class": self.aliases[alias]})
        return FakeResponse(404, None)

    # -- data ---------------------------------------------------------------
    def _resolve(self, name):
        return self.aliases.get(name, name)

    def _store(self, obj):
        name = self._resolve(obj["class"])
        if name not in self.classes:
            return {"id": obj["id"], "result": {"errors": {"error": [
                {"message": f"class {name} does not exist"}]}}}
        declared = {p["name"] for p in self.classes[name]["properties"]}
        unknown = set(obj.get("properties") or {}) - declared
        if unknown:
            return {"id": obj["id"], "result": {"errors": {"error": [
                {"message": f"no such prop with name '{sorted(unknown)[0]}'"}]}}}
        # replace, never merge: what the adapter has to work around
        self.objects[name][obj["id"]] = {"properties": dict(obj.get("properties") or {}),
                                         "vector": list(obj.get("vector") or [])}
        return {"id": obj["id"], "result": {}}

    def _check_paths(self, name, where):
        if not where:
            return
        if "operands" in where:
            for operand in where["operands"]:
                self._check_paths(name, operand)
            return
        prop = where["path"][0]
        declared = {p["name"] for p in self.classes[name]["properties"]}
        if prop != "id" and prop not in declared:
            raise ValueError(f"no such prop with name '{prop}' found in class '{name}'")
        if where["operator"] == "IsNull" and not (
                self.classes[name].get("invertedIndexConfig") or {}).get("indexNullState"):
            raise ValueError("IsNull needs indexNullState")

    def _matches(self, object_id, stored, where):
        if not where:
            return True
        operator = where["operator"]
        if operator == "And":
            return all(self._matches(object_id, stored, o) for o in where["operands"])
        if operator == "Or":
            return any(self._matches(object_id, stored, o) for o in where["operands"])
        prop = where["path"][0]
        actual = object_id if prop == "id" else stored["properties"].get(prop)
        if operator == "IsNull":
            return (actual is None) == where["valueBoolean"]
        if actual is None:
            return False
        key = next(k for k in where if k.startswith("value"))
        expected = where[key]
        if key.startswith("valueDate"):
            actual = _moment(actual)
            expected = ([_moment(v) for v in expected] if isinstance(expected, list)
                        else _moment(expected))
        if operator == "Equal":
            return actual == expected
        if operator == "NotEqual":
            return actual != expected
        if operator == "ContainsAny":
            return actual in expected
        return {"GreaterThan": actual > expected,
                "GreaterThanEqual": actual >= expected,
                "LessThan": actual < expected,
                "LessThanEqual": actual <= expected}[operator]

    def _batch_delete(self, body):
        name = self._resolve(body["match"]["class"])
        where = body["match"]["where"]
        self._check_paths(name, where)
        matching = [i for i, o in self.objects[name].items()
                    if self._matches(i, o, where)]
        for object_id in matching[:self.delete_limit]:
            del self.objects[name][object_id]
        done = min(len(matching), self.delete_limit)
        return FakeResponse(200, {"results": {"matches": len(matching),
                                              "limit": self.delete_limit,
                                              "successful": done, "failed": 0}})

    def _distance(self, name, a, b):
        metric = self.classes[name]["vectorIndexConfig"]["distance"]
        if metric == "l2-squared":
            return sum((x - y) ** 2 for x, y in zip(a, b))
        dot = sum(x * y for x, y in zip(a, b))
        if metric == "dot":
            return -dot
        norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return 1.0 - (dot / norm if norm else 0.0)

    def _graphql(self, query):
        head = re.match(r"^\{ (Get|Aggregate) \{ (\w+)", query)
        kind, name = head.group(1), self._resolve(head.group(2))
        if name not in self.classes:
            raise ValueError(f"class {name} not found")
        rest = query[head.end():].lstrip()
        args = {}
        if rest.startswith("("):
            end = _closing(rest, 0, "(", ")")
            args = json.loads("{" + _gql_to_json(rest[1:end]) + "}")
            rest = rest[end + 1:].lstrip()
        where = args.get("where")
        self._check_paths(name, where)
        rows = [(i, o) for i, o in self.objects[name].items()
                if self._matches(i, o, where)]
        if kind == "Aggregate":
            return {"Aggregate": {head.group(2): [{"meta": {"count": len(rows)}}]}}
        body = rest[1:_closing(rest, 0, "{", "}")]
        additional = re.search(r"_additional \{ ([^}]*) \}", body)
        extra = additional.group(1).split() if additional else []
        fields = body[:additional.start()].split() if additional else body.split()
        if "nearVector" in args:
            vector = args["nearVector"]["vector"]
            rows = sorted(((i, o, self._distance(name, o["vector"], vector))
                           for i, o in rows), key=lambda r: r[2])
        else:
            rows = [(i, o, None) for i, o in rows]
        offset, limit = int(args.get("offset", 0)), int(args.get("limit", 100))
        if offset + limit > self.max_results:
            raise ValueError("query maximum results exceeded")
        out = []
        for object_id, stored, distance in rows[offset:offset + limit]:
            row = {f: stored["properties"].get(f) for f in fields}
            more = {}
            if "id" in extra:
                more["id"] = object_id
            if "vector" in extra:
                more["vector"] = list(stored["vector"])
            if "distance" in extra:
                more["distance"] = distance
            row["_additional"] = more
            out.append(row)
        return {"Get": {head.group(2): out}}
