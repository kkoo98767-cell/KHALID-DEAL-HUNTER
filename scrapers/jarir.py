"""Anonymous public search/catalog used by Jarir's own Nuxt storefront."""
import json
import re
from urllib.parse import quote
from bs4 import BeautifulSoup
from .common import _normalise_product, _number


def discover(session, html, query, headers, limit=20):
    node = BeautifulSoup(html,"html.parser").select_one("#__NUXT_DATA__")
    if not node:
        return []
    data = json.loads(node.string or node.get_text())
    if not isinstance(data,list):
        return []
    state = next((v for v in data if isinstance(v,dict) and "$ssiteConfig:searchProviderKeys" in v),None)
    if not state:
        return []
    keys = data[state["$ssiteConfig:searchProviderKeys"]]
    language = "ar" if re.search(r"[\u0600-\u06ff]",query) else "en"
    key = data[keys[language]]
    if not isinstance(key,str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,100}",key):
        raise ValueError("Invalid public search index configuration")
    response = session.get("https://ac.cnstrc.com/v1/search/"+quote(query,safe=""),params={"key":key,"num_results_per_page":min(limit,20),"c":"cio-fe-web-jarir"},timeout=(5,18))
    if response.status_code != 200:
        raise RuntimeError(f"Jarir public search HTTP {response.status_code}")
    hits = response.json().get("response",{}).get("results",[])
    skus = list(dict.fromkeys(str(h.get("data",{}).get("sku") or h.get("data",{}).get("id") or "") for h in hits))
    skus = [sku for sku in skus if re.fullmatch(r"[0-9]{3,12}",sku)][:limit]
    if not skus:
        return []
    url = "https://www.jarir.com/api/catalogv2/product/store/sa-en/visibilityAll/true/sku/"+",".join(skus)+"/size/"+str(len(skus))
    response = session.get(url,headers=headers,timeout=(5,18))
    if response.status_code != 200:
        raise RuntimeError(f"Jarir public catalog HTTP {response.status_code}")
    return parse_catalog(response.json(),set(skus))


def parse_catalog(payload, requested):
    items=[]
    for hit in payload.get("data",{}).get("hits",{}).get("hits",[]):
        obj=hit.get("_source",{})
        if str(obj.get("sku")) not in requested or obj.get("type_id") != "simple":
            continue
        price = _number(obj.get("jarir_final_price") if obj.get("jarir_final_price") is not None else obj.get("final_price"))
        item=_normalise_product(obj.get("name"),price,"/sa-en/"+str(obj.get("url_path","")).lstrip("/"),"Jarir","https://www.jarir.com/sa-en/",source="jarir_public_catalog")
        if not item or not obj.get("url_path"):
            continue
        item.update(product_id=str(obj["sku"]),brand=obj.get("brand"))
        original=_number(obj.get("regular_price"))
        if original and original>price:
            item["original_price"]=original
        for key in ("gtin","model","condition","color","capacity"):
            if isinstance(obj.get(key),(str,int)):
                item[key]=str(obj[key])
        items.append(item)
    return items
