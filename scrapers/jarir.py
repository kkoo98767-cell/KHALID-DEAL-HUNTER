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
    from config import MAX_SEARCH_PAGES, REQUEST_GAP_SECONDS
    import time
    items=[]
    seen=set()
    for page in range(MAX_SEARCH_PAGES):
        try:
            response=session.get("https://ac.cnstrc.com/v1/search/"+quote(query,safe=""),params={"key":key,"num_results_per_page":20,"offset":page*20,"c":"cio-fe-web-jarir"},timeout=(5,18))
            if response.status_code!=200:
                raise PublicSourceError(f"Jarir public search HTTP {response.status_code}",items,response.status_code in {401,403,429})
            payload=response.json().get("response",{})
            hits=payload.get("results",[])
            skus=list(dict.fromkeys(str(h.get("data",{}).get("sku") or h.get("data",{}).get("id") or "") for h in hits))
            skus=[sku for sku in skus if re.fullmatch(r"[0-9]{3,12}",sku) and sku not in seen]
            if not skus:
                break
            seen.update(skus)
            time.sleep(REQUEST_GAP_SECONDS)
            for item in fetch_catalog(session,skus,headers):
                if item["url"] not in {p["url"] for p in items}:
                    items.append(item)
            if len(items)>=limit or len(hits)<20 or page*20+len(hits)>=payload.get("total_num_results",10**9):
                break
            time.sleep(REQUEST_GAP_SECONDS)
        except PublicSourceError as exc:
            exc.products=list({p["url"]:p for p in items+exc.products}.values())
            raise
        except Exception as exc:
            raise PublicSourceError(f"Jarir public source {type(exc).__name__}",items) from None
    return items[:limit]


class PublicSourceError(RuntimeError):
    def __init__(self,message,products=None,blocked=False):
        super().__init__(message)
        self.products=products or []
        self.blocked=blocked


def fetch_catalog(session,skus,headers):
    """Public catalog GET, in batches matching the storefront's 20-SKU limit."""
    items=[]
    for offset in range(0,len(skus),20):
        batch=skus[offset:offset+20]
        url="https://www.jarir.com/api/catalogv2/product/store/sa-en/visibilityAll/true/sku/"+",".join(batch)+"/size/"+str(len(batch))
        response=session.get(url,headers=headers,timeout=(5,18))
        if response.status_code!=200:
            raise PublicSourceError(f"Jarir public catalog HTTP {response.status_code}",items,response.status_code in {401,403,429})
        items.extend(parse_catalog(response.json(),set(batch)))
    return items


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
        from .common import image_url
        picture=image_url(obj.get("image_url") or obj.get("image") or obj.get("thumbnail"),"https://www.jarir.com/")
        if picture:
            item["image_url"]=picture
        original=_number(obj.get("regular_price"))
        if original and original>price:
            item["original_price"]=original
        for key in ("gtin","model","condition","color","capacity"):
            if isinstance(obj.get(key),(str,int)):
                item[key]=str(obj[key])
        items.append(item)
    return items
