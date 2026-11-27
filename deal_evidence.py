"""Deterministic money, identity and evidence scores. No AI verification."""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import datetime, timezone
import re
from config import MIN_DROP_PERCENT


def money(value):
    try:
        d=Decimal(str(value))
        return d.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP) if d.is_finite() and d>=0 else None
    except (InvalidOperation,ValueError,TypeError):
        return None


def savings(reference,current):
    old,now=money(reference),money(current)
    if old is None or now is None or old<=0:
        return {"amount_sar":None,"percent":None}
    amount=max(Decimal(0),old-now)
    return {"amount_sar":float(amount),"percent":float((amount/old*100).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP))}


def valid_gtin(value):
    text=str(value or "")
    if not re.fullmatch(r"(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})",text) or len(set(text))==1:
        return False
    weighted=sum(int(c)*(3 if i%2==0 else 1) for i,c in enumerate(reversed(text[:-1])))
    return (10-weighted%10)%10==int(text[-1])


def age_hours(stamp,now=None):
    try:
        d=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
        age=((now or datetime.now(timezone.utc))-d).total_seconds()/3600
        return age if age>=0 else float("inf")
    except (ValueError,TypeError,AttributeError):
        return float("inf")


def matching_offers(product,candidates):
    if not valid_gtin(product.get("gtin")) or age_hours(product.get("last_seen"))>24 or not product.get("latest_validated",True) or product.get("status")=="DATA_ANOMALY":
        return []
    matches=[]
    for other in candidates:
        if not other.get("latest_validated",True) or other["store"]==product["store"] or other.get("gtin")!=product["gtin"] or age_hours(other.get("last_seen"))>24 or other.get("currency")!="SAR" or other.get("status")=="DATA_ANOMALY":
            continue
        mismatch=any(product.get(k) and other.get(k) and str(product[k]).casefold()!=str(other[k]).casefold() for k in ("brand","model","color","capacity","size","condition"))
        if mismatch:continue
        confirmed=str(product.get("condition", "")).casefold() in {"new","used","refurbished"} and str(product.get("condition", "")).casefold()==str(other.get("condition", "")).casefold()
        matches.append({"store":other["store"],"price":other["price"],"url":other["url"],"last_seen":other["last_seen"],"match":"GTIN", "condition_confirmed":confirmed,"shipping_sar":other.get("shipping_sar"),"shipping_included":False})
    return sorted(matches,key=lambda o:o["price"])


def evaluate(product,offers):
    historical=savings(product.get("baseline"),product["price"])
    advertised=savings(product.get("store_original_price"),product["price"])
    age=age_hours(product.get("last_seen"))
    anomaly=product.get("status")=="DATA_ANOMALY"
    fresh=age<=24
    has_history=product.get("baseline") is not None
    latest_validated=product.get("latest_validated",True)
    verified=has_history and fresh and not anomaly and latest_validated
    confirmed=[o for o in offers if o["condition_confirmed"]]
    components={"identity":10 if product.get("product_id") or valid_gtin(product.get("gtin")) else 3,
                "freshness":20 if fresh else 10 if age<=72 else 0,
                "history":50 if has_history else min(15,product.get("history_days",0)*2),
                "cross_store":20 if confirmed else 0}
    score=sum(components.values())
    if not verified:score=min(score,39)
    if anomaly:score=0
    warnings=[]
    if not latest_validated:warnings.append("آخر قراءة غير مدققة ولا تصلح لإثبات خصم")
    if not has_history:warnings.append("التاريخ السعري غير كافٍ للتحقق من الخصم")
    if not fresh:warnings.append("السعر أقدم من يوم؛ يجب تحديثه قبل الاعتماد عليه")
    if not offers:warnings.append("لا توجد مطابقة موثوقة حديثة عبر المتاجر؛ لا نقارن المنتجات بتشابه الاسم")
    elif not confirmed:warnings.append("مطابقة GTIN متاحة، لكن حالة السلعة غير مؤكدة؛ المقارنة أولية")
    if offers:warnings.append("راجع تكاليف الشحن المتاحة وحالة السلعة قبل الشراء")
    claim="NO_REFERENCE" if advertised["percent"] is None else "UNVERIFIED"
    if advertised["percent"] is not None and verified:
        claim="OVERSTATED_VS_HISTORY" if advertised["percent"]-historical["percent"]>=10 else "CONSISTENT_WITH_HISTORY"
    if claim=="OVERSTATED_VS_HISTORY":warnings.append("الخصم المعلن أكبر من الانخفاض المدعوم بالسجل؛ هذا اختلاف مرجعي وليس إثباتاً للاحتيال")
    if anomaly:warnings.append("هوية المنتج أو بياناته تحتاج مراجعة")
    positive_savings=historical["amount_sar"] is not None and historical["amount_sar"]>0
    verified_deal=verified and positive_savings and product.get("status")=="VERIFIED_DEAL"
    featured=fresh and latest_validated and not anomaly and ((advertised["percent"] is not None and advertised["percent"]>=MIN_DROP_PERCENT) or (verified and historical["percent"] is not None and historical["percent"]>=MIN_DROP_PERCENT))
    return {"featured":featured,"discovery_threshold_percent":MIN_DROP_PERCENT,"historical_savings":historical,"advertised_savings":advertised,"historical_evidence_valid":verified,
            "verified_deal":verified_deal,
            "claim_assessment":claim,"trust_score":score,"trust_components":components,"warnings":warnings,
            "cross_store_offers":offers,"lowest_matched_price":min((o["price"] for o in offers),default=None),
            "value_assessment":"لا يمثل تقييم الثقة درجة لجودة المنتج أو توصية بالشراء"}
