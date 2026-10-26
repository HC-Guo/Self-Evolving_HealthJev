from __future__ import annotations

import json
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode


HTTP_TIMEOUT = 20
USER_AGENT = "HealthClaw-task-tool-router/1.0"

import requests as _requests

_GENE_PROXY = os.environ.get("HEALTHJEV_GENE_PROXY")
_GENE_PROXIES = {"http": _GENE_PROXY, "https": _GENE_PROXY} if _GENE_PROXY else {}


def _proxied_get(url: str, params=None, headers=None, timeout: int = HTTP_TIMEOUT):
    last = None
    for attempt in range(3):
        try:
            r = _requests.get(url, params=params, headers=headers,
                              proxies=_GENE_PROXIES, timeout=timeout)
            if r.status_code in (502, 503, 504, 429):
                last = f"HTTP {r.status_code}"
            else:
                return r
        except Exception as exc:
            last = exc
        time.sleep(1.0 + attempt)
    raise RuntimeError(f"gene reference request failed after retries: {last}")


def _proxied_post(url: str, json_body=None, headers=None, timeout: int = HTTP_TIMEOUT):
    last = None
    for attempt in range(3):
        try:
            r = _requests.post(url, json=json_body, headers=headers,
                               proxies=_GENE_PROXIES, timeout=timeout)
            if r.status_code in (502, 503, 504, 429):
                last = f"HTTP {r.status_code}"
            else:
                return r
        except Exception as exc:
            last = exc
        time.sleep(1.0 + attempt)
    raise RuntimeError(f"gene reference request failed after retries: {last}")
OPENTARGETS_GRAPHQL = "https://api.platform.opentargets.org/api/v4/graphql"
BLAST_CACHE_ENV = "HEALTHCLAW_BLAST_CACHE_DIR"
GENE_TOKEN_RE = re.compile(r"\b(?:ENSG\d{8,}|[A-Z][A-Z0-9-]{2,})\b")
RSID_RE = re.compile(r"\brs\d+\b", re.IGNORECASE)
DNA_RE = re.compile(r"\b[ACGTN]{40,}\b", re.IGNORECASE)

CHR_ACCESSIONS = {
    **{f"NC_{idx:06d}": str(idx) for idx in range(1, 23)},
    "NC_000023": "X",
    "NC_000024": "Y",
}
SPECIES_ALIASES = {
    "gallus gallus": "chicken",
    "chicken": "chicken",
    "danio rerio": "zebrafish",
    "zebrafish": "zebrafish",
    "caenorhabditis elegans": "worm",
    "c. elegans": "worm",
    "saccharomyces cerevisiae": "yeast",
    "yeast": "yeast",
    "mus musculus": "mouse",
    "house mouse": "mouse",
    "rattus norvegicus": "rat",
    "norway rat": "rat",
    "homo sapiens": "human",
    "human": "human",
}


def _get_json(url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    r = _proxied_get(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    raw = r.text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_text": raw[:2000]}
    return data if isinstance(data, dict) else {"items": data}


def _post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    r = _proxied_post(url, payload,
                      headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", **(headers or {})})
    raw = r.text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_text": raw[:2000]}
    return data if isinstance(data, dict) else {"items": data}


def _safe_get(obj: Any, path: Iterable[str], default: Any = None) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _unique_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _blast_cache_dir() -> Path:
    default = Path(__file__).resolve().parents[2] / "data" / "blast_cache"
    return Path(os.environ.get(BLAST_CACHE_ENV) or default).expanduser()


def _blast_cache_key(task_kind: str, sequence: str, database: str) -> str:
    payload = json.dumps(
        {"task_kind": task_kind, "database": database, "sequence": sequence},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_blast_cache(task_kind: str, sequence: str, database: str) -> Optional[Dict[str, Any]]:
    path = _blast_cache_dir() / f"{_blast_cache_key(task_kind, sequence, database)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_blast_cache(task_kind: str, sequence: str, database: str, data: Dict[str, Any]) -> None:
    path = _blast_cache_dir() / f"{_blast_cache_key(task_kind, sequence, database)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _first_gene_token(text: str) -> Optional[str]:
    candidates = [m.group(0) for m in GENE_TOKEN_RE.finditer(text or "")]
    stop = {
        "DNA",
        "SNP",
        "RNA",
        "GENE",
        "GENOME",
        "HUMAN",
        "CHRONIC",
        "WHICH",
        "WHAT",
        "ALIGN",
        "CONVERT",
    }
    for token in candidates:
        if token.upper() not in stop:
            return token
    return None


def _first_rsid(text: str) -> Optional[str]:
    m = RSID_RE.search(text or "")
    return m.group(0).lower() if m else None


def _first_dna_sequence(text: str) -> Optional[str]:
    m = DNA_RE.search((text or "").replace("\n", " "))
    return m.group(0).upper() if m else None


def _mygene_query(term: str, species: str = "human", size: int = 5) -> List[Dict[str, Any]]:
    fields = ",".join(
        [
            "symbol",
            "name",
            "entrezgene",
            "ensembl.gene",
            "genomic_pos",
            "genomic_pos_hg19",
            "type_of_gene",
            "alias",
            "summary",
        ]
    )
    data = _get_json(
        "https://mygene.info/v3/query",
        {"q": term, "species": species, "fields": fields, "size": size},
    )
    hits = data.get("hits") if isinstance(data.get("hits"), list) else []
    out = []
    for hit in hits[:size]:
        if isinstance(hit, dict):
            out.append(hit)
    return out


def _hgnc_search(term: str, size: int = 5) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    seen = set()
    for field in ["symbol", "alias_symbol", "prev_symbol", "name"]:
        try:
            data = _get_json(
                f"https://rest.genenames.org/search/{field}/{term}",
                headers={"Accept": "application/json"},
            )
        except Exception:
            continue
        response = data.get("response") if isinstance(data.get("response"), dict) else {}
        for doc in response.get("docs", []) or []:
            if not isinstance(doc, dict):
                continue
            key = doc.get("hgnc_id") or doc.get("symbol") or json.dumps(doc, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            docs.append(doc)
            if len(docs) >= size:
                return docs
    return docs


def _ensembl_variation(rsid: str, species: str = "human") -> Dict[str, Any]:
    species = species or "human"
    return _get_json(
        f"https://rest.ensembl.org/variation/{species}/{rsid}",
        headers={"Content-Type": "application/json"},
    )


def _myvariant_query(rsid: str, size: int = 3) -> List[Dict[str, Any]]:
    data = _get_json(
        "https://myvariant.info/v1/query",
        {
            "q": f"dbsnp.rsid:{rsid}",
            "fields": "dbsnp,dbnsfp,clinvar,snpeff,cadd,vcf",
            "size": size,
        },
    )
    hits = data.get("hits") if isinstance(data.get("hits"), list) else []
    return [h for h in hits[:size] if isinstance(h, dict)]


def _ncbi_gene_search(term: str, retmax: int = 5) -> List[Dict[str, Any]]:
    if not term:
        return []
    search = _get_json(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        {"db": "gene", "term": f"{term} AND Homo sapiens[orgn]", "retmode": "json", "retmax": retmax},
    )
    ids = _safe_get(search, ["esearchresult", "idlist"], []) or []
    if not ids:
        return []
    summary = _get_json(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
        {"db": "gene", "id": ",".join(ids[:retmax]), "retmode": "json"},
    )
    result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
    out = []
    for gid in result.get("uids", []) or []:
        item = result.get(str(gid))
        if isinstance(item, dict):
            out.append(item)
    return out


def _opentargets_search_disease(term: str, size: int = 3) -> List[Dict[str, Any]]:
    query = """
    query searchDisease($queryString: String!) {
      search(queryString: $queryString, entityNames: ["disease"], page: {index: 0, size: 3}) {
        hits { id name entity }
      }
    }
    """
    data = _post_json(OPENTARGETS_GRAPHQL, {"query": query, "variables": {"queryString": term}})
    hits = _safe_get(data, ["data", "search", "hits"], []) or []
    return [h for h in hits[:size] if isinstance(h, dict)]


def _opentargets_associated_targets(disease_id: str, size: int = 10) -> Dict[str, Any]:
    query = """
    query diseaseTargets($efoId: String!, $size: Int!) {
      disease(efoId: $efoId) {
        id
        name
        associatedTargets(page: {index: 0, size: $size}) {
          rows {
            score
            target { id approvedSymbol approvedName }
          }
        }
      }
    }
    """
    return _post_json(
        OPENTARGETS_GRAPHQL,
        {"query": query, "variables": {"efoId": disease_id, "size": size}},
    )


def disease_gene_reference_lookup(disease: str, size: int = 10) -> Dict[str, Any]:
    disease = (disease or "").strip(" ?.")
    started = time.time()
    if not disease:
        return {"status": "error", "msg": "disease is empty", "input_visible_only": True}

    warnings: List[str] = []
    disease_hits: List[Dict[str, Any]] = []
    ot_rows: List[Dict[str, Any]] = []
    try:
        disease_hits = _opentargets_search_disease(disease)
        if disease_hits:
            data = _opentargets_associated_targets(str(disease_hits[0].get("id")), size=size)
            rows = _safe_get(data, ["data", "disease", "associatedTargets", "rows"], []) or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                target = row.get("target") if isinstance(row.get("target"), dict) else {}
                ot_rows.append({
                    "source": "Open Targets Platform",
                    "disease_id": disease_hits[0].get("id"),
                    "disease_name": disease_hits[0].get("name"),
                    "score": row.get("score"),
                    "symbol": target.get("approvedSymbol"),
                    "ensembl_gene_id": target.get("id"),
                    "name": target.get("approvedName"),
                })
    except Exception as exc:
        warnings.append(f"Open Targets failed: {exc}")

    ncbi_rows: List[Dict[str, Any]] = []
    try:
        for item in _ncbi_gene_search(disease, retmax=5):
            ncbi_rows.append({
                "source": "NCBI Gene E-utilities",
                "symbol": item.get("name") or item.get("nomenclaturesymbol"),
                "description": item.get("description"),
                "chromosome": item.get("chromosome"),
                "summary": str(item.get("summary") or "")[:600],
            })
    except Exception as exc:
        warnings.append(f"NCBI Gene search failed: {exc}")

    symbols = _unique_strings([x.get("symbol") for x in ot_rows] + [x.get("symbol") for x in ncbi_rows])
    return {
        "status": "success" if symbols else "not_found",
        "tool_name": "disease_gene_reference_lookup",
        "input_visible_only": True,
        "query": disease,
        "latency_ms": int((time.time() - started) * 1000),
        "answer_hints": {
            "answer_type": "gene_symbol",
            "candidate_answer": symbols[0] if symbols else None,
            "candidate_answers": symbols[:size],
        },
        "disease_matches": disease_hits,
        "associated_targets": ot_rows[:size],
        "ncbi_gene_results": ncbi_rows[:5],
        "source_services": ["Open Targets Platform GraphQL API", "NCBI E-utilities"],
        "warnings": warnings,
        "gold_label_used": False,
        "future_cases_used": False,
    }


def _gene_result_from_mygene(hit: Dict[str, Any]) -> Dict[str, Any]:
    genomic = hit.get("genomic_pos") or hit.get("genomic_pos_hg19") or {}
    if isinstance(genomic, list):
        genomic = genomic[0] if genomic else {}
    if not isinstance(genomic, dict):
        genomic = {}
    ensembl = hit.get("ensembl") or {}
    if isinstance(ensembl, list):
        ensembl_ids = [x.get("gene") for x in ensembl if isinstance(x, dict)]
    elif isinstance(ensembl, dict):
        ensembl_ids = _as_list(ensembl.get("gene"))
    else:
        ensembl_ids = []
    return {
        "source": "MyGene.info",
        "symbol": hit.get("symbol"),
        "name": hit.get("name"),
        "entrezgene": hit.get("entrezgene"),
        "ensembl_gene_ids": _unique_strings(ensembl_ids),
        "chromosome": str(genomic.get("chr")) if genomic.get("chr") is not None else None,
        "start": genomic.get("start"),
        "end": genomic.get("end"),
        "strand": genomic.get("strand"),
        "type_of_gene": hit.get("type_of_gene"),
        "aliases": _unique_strings(_as_list(hit.get("alias")))[:20],
        "summary": str(hit.get("summary") or "")[:600],
        "score": hit.get("_score"),
    }


def _gene_result_from_hgnc(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": "HGNC REST",
        "symbol": doc.get("symbol"),
        "name": doc.get("name"),
        "hgnc_id": doc.get("hgnc_id"),
        "entrez_id": doc.get("entrez_id"),
        "ensembl_gene_id": doc.get("ensembl_gene_id"),
        "chromosome": doc.get("location"),
        "locus_type": doc.get("locus_type"),
        "status": doc.get("status"),
        "alias_symbols": _unique_strings(_as_list(doc.get("alias_symbol")))[:20],
        "previous_symbols": _unique_strings(_as_list(doc.get("prev_symbol")))[:20],
    }


def gene_reference_lookup(term: str, species: str = "human") -> Dict[str, Any]:
    term = (term or "").strip()
    started = time.time()
    if not term:
        return {"status": "error", "msg": "term is empty", "input_visible_only": True}
    errors: List[str] = []
    mygene_results: List[Dict[str, Any]] = []
    hgnc_results: List[Dict[str, Any]] = []
    try:
        mygene_results = [_gene_result_from_mygene(x) for x in _mygene_query(term, species=species)]
    except Exception as exc:
        errors.append(f"MyGene.info failed: {exc}")
    try:
        hgnc_results = [_gene_result_from_hgnc(x) for x in _hgnc_search(term)]
    except Exception as exc:
        errors.append(f"HGNC REST failed: {exc}")

    ranked = mygene_results + [x for x in hgnc_results if x.get("symbol") not in {m.get("symbol") for m in mygene_results}]
    top = ranked[0] if ranked else {}
    answer_hints = {
        "official_symbol": top.get("symbol"),
        "chromosome": top.get("chromosome"),
        "is_protein_coding": None,
    }
    type_text = str(top.get("type_of_gene") or top.get("locus_type") or "").lower()
    if type_text:
        answer_hints["is_protein_coding"] = "protein-coding" in type_text or "protein coding" in type_text
    return {
        "status": "success" if ranked else "not_found",
        "tool_name": "gene_reference_lookup",
        "input_visible_only": True,
        "query": term,
        "species": species,
        "latency_ms": int((time.time() - started) * 1000),
        "answer_hints": answer_hints,
        "top_results": ranked[:5],
        "source_services": ["MyGene.info", "HGNC REST"],
        "warnings": errors,
    }


def _extract_gene_symbols_from_myvariant(hit: Dict[str, Any]) -> List[str]:
    symbols: List[Any] = []
    dbsnp_gene = _safe_get(hit, ["dbsnp", "gene"])
    for item in _as_list(dbsnp_gene):
        if isinstance(item, dict):
            symbols.extend([item.get("symbol"), item.get("name")])
            for rna in _as_list(item.get("rnas")):
                if isinstance(rna, dict):
                    symbols.append(rna.get("gene"))
    for item in _as_list(_safe_get(hit, ["dbnsfp", "gene"])):
        if isinstance(item, dict):
            symbols.append(item.get("genename"))
    snpeff_gene = _safe_get(hit, ["snpeff", "ann", "genename"])
    symbols.extend(_as_list(snpeff_gene))
    return _unique_strings(symbols)


def variant_reference_lookup(rsid: str, species: str = "human") -> Dict[str, Any]:
    rsid = (_first_rsid(rsid) or rsid or "").strip().lower()
    started = time.time()
    if not rsid:
        return {"status": "error", "msg": "rsid is empty", "input_visible_only": True}
    errors: List[str] = []
    ensembl: Dict[str, Any] = {}
    myvariant_hits: List[Dict[str, Any]] = []
    try:
        ensembl = _ensembl_variation(rsid, species=species)
    except Exception as exc:
        errors.append(f"Ensembl REST failed: {exc}")
    try:
        myvariant_hits = _myvariant_query(rsid)
    except Exception as exc:
        errors.append(f"MyVariant.info failed: {exc}")

    mappings = []
    for m in ensembl.get("mappings", []) if isinstance(ensembl.get("mappings"), list) else []:
        if isinstance(m, dict):
            mappings.append({
                "assembly_name": m.get("assembly_name"),
                "chromosome": str(m.get("seq_region_name")) if m.get("seq_region_name") is not None else None,
                "location": m.get("location"),
                "allele_string": m.get("allele_string"),
                "start": m.get("start"),
                "end": m.get("end"),
            })
    gene_symbols: List[str] = []
    compact_hits: List[Dict[str, Any]] = []
    for hit in myvariant_hits:
        genes = _extract_gene_symbols_from_myvariant(hit)
        gene_symbols.extend(genes)
        compact_hits.append({
            "source": "MyVariant.info",
            "variant_id": hit.get("_id"),
            "chromosome": hit.get("chrom") or _safe_get(hit, ["vcf", "chrom"]),
            "genes": genes[:10],
            "most_severe_consequence": _safe_get(hit, ["snpeff", "ann", "effect"])
            or _safe_get(hit, ["cadd", "gene", "feature_id"]),
        })
    chrom = mappings[0].get("chromosome") if mappings else (compact_hits[0].get("chromosome") if compact_hits else None)
    return {
        "status": "success" if mappings or compact_hits else "not_found",
        "tool_name": "variant_reference_lookup",
        "input_visible_only": True,
        "query": rsid,
        "species": species,
        "latency_ms": int((time.time() - started) * 1000),
        "answer_hints": {
            "chromosome": f"chr{chrom}" if chrom and not str(chrom).lower().startswith("chr") else chrom,
            "associated_genes": _unique_strings(gene_symbols)[:10],
        },
        "ensembl_mappings": mappings[:5],
        "myvariant_hits": compact_hits[:3],
        "source_services": ["Ensembl REST", "MyVariant.info"],
        "warnings": errors,
    }


def _disease_phrase(question: str) -> str:
    text = question or ""
    patterns = [
        r"genes related to\s+(.+?)\??$",
        r"genes associated with\s+(.+?)\??$",
        r"gene associated with\s+(.+?)\??$",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip(" ?.")
    return text.strip(" ?.")


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _chromosome_from_blast_hit(hit: Dict[str, Any]) -> Optional[str]:
    accession = str(hit.get("accession") or "")
    for prefix, chrom in CHR_ACCESSIONS.items():
        if accession.startswith(prefix):
            return chrom
    text = f"{hit.get('id') or ''} {hit.get('def') or ''}"
    match = re.search(r"\bchromosome\s+([0-9]{1,2}|X|Y)\b", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def _best_hsp(hit: Dict[str, Any]) -> Dict[str, Any]:
    hsps = [h for h in hit.get("hsps") or [] if isinstance(h, dict)]
    if not hsps:
        return {}
    return sorted(
        hsps,
        key=lambda h: (
            -(_safe_float(h.get("bit_score")) or 0.0),
            _safe_float(h.get("evalue")) or 1e99,
        ),
    )[0]


def _identity_fraction(hsp: Dict[str, Any]) -> Optional[float]:
    ident = _safe_int(hsp.get("identity"))
    length = _safe_int(hsp.get("align_len"))
    if ident is None or not length:
        return None
    return ident / length


def _human_alignment_candidates(blast: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for hit in blast.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        chrom = _chromosome_from_blast_hit(hit)
        hsp = _best_hsp(hit)
        start = _safe_int(hsp.get("hit_from"))
        end = _safe_int(hsp.get("hit_to"))
        if not chrom or start is None or end is None:
            continue
        left, right = sorted([start, end])
        label = f"chr{chrom}:{left}-{right}"
        candidates.append({
            "label": label,
            "chromosome": f"chr{chrom}",
            "start": left,
            "end": right,
            "accession": hit.get("accession"),
            "hit_id": hit.get("id"),
            "hit_def": hit.get("def"),
            "identity_fraction": _identity_fraction(hsp),
            "evalue": _safe_float(hsp.get("evalue")),
            "bit_score": _safe_float(hsp.get("bit_score")),
        })
    candidates.sort(key=lambda x: (-(x.get("identity_fraction") or 0.0), x.get("evalue") or 1e99, -(x.get("bit_score") or 0.0)))
    return candidates


def _species_from_text(text: str) -> Optional[str]:
    low = (text or "").lower()
    for needle, label in SPECIES_ALIASES.items():
        if needle in low:
            return label
    return None


def _multispecies_candidates(blast: Dict[str, Any]) -> List[Dict[str, Any]]:
    ranked: Dict[str, Dict[str, Any]] = {}
    for hit in blast.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        hsp = _best_hsp(hit)
        label = _species_from_text(f"{hit.get('def') or ''} {hit.get('id') or ''}")
        if not label:
            continue
        score = _safe_float(hsp.get("bit_score")) or 0.0
        identity = _identity_fraction(hsp) or 0.0
        prev = ranked.get(label)
        if prev and (prev.get("bit_score") or 0.0) >= score:
            continue
        ranked[label] = {
            "label": label,
            "bit_score": score,
            "identity_fraction": identity,
            "evalue": _safe_float(hsp.get("evalue")),
            "accession": hit.get("accession"),
            "hit_id": hit.get("id"),
            "hit_def": hit.get("def"),
        }
    out = list(ranked.values())
    out.sort(key=lambda x: (-(x.get("identity_fraction") or 0.0), x.get("evalue") or 1e99, -(x.get("bit_score") or 0.0)))
    return out


def sequence_alignment_reference_lookup(
    sequence: str,
    task_name: str = "",
    max_wait_seconds: int = 75,
    use_cache: bool = True,
) -> Dict[str, Any]:
    seq = re.sub(r"\s+", "", sequence or "").upper()
    started = time.time()
    if len(seq) < 40:
        return {
            "status": "error",
            "tool_name": "sequence_alignment_reference_lookup",
            "input_visible_only": True,
            "message": "DNA sequence is missing or too short.",
            "gold_label_used": False,
            "future_cases_used": False,
        }

    task = (task_name or "").lower()
    human_task = "human genome" in task or "human genome" in task_name.lower()
    task_kind = "human_genome_alignment" if human_task else "multi_species_alignment"
    database = "GPIPE/9606/current/ref_top_level" if human_task else "nt"
    cached = _read_blast_cache(task_kind, seq, database) if use_cache else None
    if cached:
        cached = dict(cached)
        cached["cache_hit"] = True
        return cached

    try:
        from tools.bioinfo_tools import blast_search

        blast = blast_search(
            seq,
            program="blastn",
            database=database,
            evalue=1e-20,
            max_wait_seconds=max_wait_seconds,
            megablast=True,
            hitlist_size=20,
            poll_interval=15,
            tool_name="HealthClaw-task-tool-router",
        )
    except Exception as exc:
        blast = {"status": "error", "msg": str(exc)[:800]}

    if human_task:
        candidates = _human_alignment_candidates(blast) if blast.get("status") == "success" else []
        answer_type = "genomic_interval"
    else:
        candidates = _multispecies_candidates(blast) if blast.get("status") == "success" else []
        answer_type = "organism"

    out = {
        "status": "success" if candidates else ("pending_or_timeout" if blast.get("rid") else "error"),
        "tool_name": "sequence_alignment_reference_lookup",
        "task_kind": task_kind,
        "database": database,
        "sequence_length": len(seq),
        "gc_fraction": round((seq.count("G") + seq.count("C")) / len(seq), 4),
        "answer_hints": {
            "answer_type": answer_type,
            "candidate_answer": candidates[0]["label"] if candidates else None,
            "candidate_answers": [c["label"] for c in candidates[:5]],
        },
        "label_candidates": candidates[:5],
        "blast_status": blast.get("status"),
        "blast_rid": blast.get("rid"),
        "blast_message": blast.get("msg"),
        "source_services": ["NCBI BLAST URL API"],
        "input_visible_only": True,
        "gold_label_used": False,
        "future_cases_used": False,
        "latency_ms": int((time.time() - started) * 1000),
        "cache_hit": False,
        "warning": (
            "Remote BLAST can be slow/rate-limited; cached results are reused. "
            "Human genome coordinates depend on the NCBI database assembly."
        ),
    }
    if blast.get("status") == "success":
        out["blast_top_hits"] = (blast.get("hits") or [])[:5]
    if use_cache:
        _write_blast_cache(task_kind, seq, database, out)
    return out


def geneturing_reference_answer_tool(
    question: str,
    task_name: str = "",
    species: str = "human",
    run_remote_alignment: bool = False,
    remote_alignment_max_wait_seconds: int = 75,
) -> Dict[str, Any]:
    question = question or ""
    task = (task_name or "").lower()
    started = time.time()
    calls: List[Dict[str, Any]] = []
    warnings: List[str] = []
    answer_hints: Dict[str, Any] = {}

    rsid = _first_rsid(question)
    gene_token = _first_gene_token(question)
    sequence = _first_dna_sequence(question)

    try:
        if "snp location" in task or ("chromosome" in question.lower() and rsid):
            ev = variant_reference_lookup(rsid or "", species=species)
            calls.append(ev)
            answer_hints["answer_type"] = "chromosome"
            answer_hints["candidate_answer"] = ev.get("answer_hints", {}).get("chromosome")
        elif "gene snp" in task or ("which gene" in question.lower() and rsid):
            ev = variant_reference_lookup(rsid or "", species=species)
            calls.append(ev)
            answer_hints["answer_type"] = "gene_symbol"
            genes = ev.get("answer_hints", {}).get("associated_genes") or []
            answer_hints["candidate_answers"] = genes
            answer_hints["candidate_answer"] = genes[0] if genes else None
        elif "gene location" in task or ("which chromosome" in question.lower() and gene_token):
            ev = gene_reference_lookup(gene_token or "", species=species)
            calls.append(ev)
            answer_hints["answer_type"] = "chromosome"
            chrom = ev.get("answer_hints", {}).get("chromosome")
            answer_hints["candidate_answer"] = f"chr{chrom}" if chrom and not str(chrom).lower().startswith("chr") else chrom
        elif "gene alias" in task or "gene name conversion" in task or "official gene symbol" in question.lower():
            ev = gene_reference_lookup(gene_token or "", species=species)
            calls.append(ev)
            answer_hints["answer_type"] = "official_gene_symbol"
            answer_hints["candidate_answer"] = ev.get("answer_hints", {}).get("official_symbol")
        elif "protein-coding" in task or "protein-coding" in question.lower() or "protein coding" in question.lower():
            ev = gene_reference_lookup(gene_token or "", species=species)
            calls.append(ev)
            value = ev.get("answer_hints", {}).get("is_protein_coding")
            answer_hints["answer_type"] = "yes_no_or_na"
            answer_hints["candidate_answer"] = "Yes" if value is True else ("NA" if value is False else None)
        elif "disease association" in task or "genes related to" in question.lower():
            disease = _disease_phrase(question)
            ev = disease_gene_reference_lookup(disease)
            calls.append(ev)
            answer_hints["answer_type"] = "gene_symbol"
            symbols = ev.get("answer_hints", {}).get("candidate_answers") or []
            answer_hints["candidate_answers"] = symbols[:5]
            answer_hints["candidate_answer"] = symbols[0] if symbols else None
            if not symbols:
                warnings.append("Disease-gene reference lookup did not return a confident answer.")
        elif sequence:
            answer_hints["answer_type"] = "sequence_alignment"
            answer_hints["sequence_length"] = len(sequence)
            answer_hints["gc_fraction"] = round((sequence.count("G") + sequence.count("C")) / len(sequence), 4) if sequence else None
            if run_remote_alignment:
                ev = sequence_alignment_reference_lookup(
                    sequence,
                    task_name=task_name,
                    max_wait_seconds=remote_alignment_max_wait_seconds,
                    use_cache=True,
                )
                calls.append(ev)
                ev_hints = ev.get("answer_hints") if isinstance(ev.get("answer_hints"), dict) else {}
                answer_hints.update({
                    "answer_type": ev_hints.get("answer_type") or answer_hints["answer_type"],
                    "candidate_answer": ev_hints.get("candidate_answer"),
                    "candidate_answers": ev_hints.get("candidate_answers") or [],
                })
                if ev.get("status") != "success":
                    warnings.append(str(ev.get("blast_message") or "Remote BLAST did not return a confident candidate."))
            else:
                calls.append({
                    "status": "not_run",
                    "tool_name": "sequence_alignment_reference_lookup",
                    "input_visible_only": True,
                    "sequence_length": len(sequence),
                    "recommended_tools": ["NCBI BLAST URL API", "local BWA/minimap2 if reference index is configured"],
                    "setup_hint": "Set HEALTHCLAW_ENABLE_REMOTE_ALIGNMENT=1 or pass run_remote_alignment=True to execute cached BLAST.",
                })
                warnings.append("Remote sequence alignment was not run by default to avoid slow/rate-limited calls.")
        else:
            answer_hints["answer_type"] = "unknown"
            warnings.append("No supported gene, rsID, disease, or DNA-sequence pattern was detected.")
    except Exception as exc:
        warnings.append(f"router failed: {exc}")

    return {
        "status": "success" if calls else "not_found",
        "tool_name": "geneturing_reference_answer_tool",
        "input_visible_only": True,
        "task_name": task_name,
        "question": question,
        "latency_ms": int((time.time() - started) * 1000),
        "answer_hints": answer_hints,
        "tool_calls": calls,
        "warnings": warnings,
        "source_services": ["MyGene.info", "MyVariant.info", "Ensembl REST", "HGNC REST", "NCBI E-utilities", "NCBI BLAST URL API"],
        "gold_label_used": False,
        "future_cases_used": False,
    }
