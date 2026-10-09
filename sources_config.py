"""
sources_config.py — Target Legal Data Sources for Contemporary and Historical Egyptian Legislation.

Configured Source Categories:
  1. Contemporary Official Gazette (2025–2026 Laws & Decrees)
  2. Presidential Decrees & Cabinet Decisions (تشكيل الحكومة، تفويضات، قرارات سيادية)
  3. Economic, Financial & Commercial Regulations (حماية المنافسة، الضرائب، الاستثمار)
  4. Historical Codes & International Treaties (المعاهدات، القوانين المدنية والجنائية)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class TargetSource:
    name: str
    base_url: str
    category: str
    target_years: List[int]
    description: str
    term_id: Optional[int] = None
    custom_endpoint: Optional[str] = None


# Registry of verified target legal sources
TARGET_LEGAL_SOURCES: Dict[str, TargetSource] = {
    # ── 1. Recent Egyptian Laws (2025 - 2026) ─────────────────────────────────
    "contemporary_laws": TargetSource(
        name="الجريدة الرسمية - القوانين الصادرة حديثاً (2025 - 2026)",
        base_url="https://manshurat.org",
        category="قانون",
        term_id=23,
        target_years=[2025, 2026],
        description="تشمل قانون الإجراءات الجنائية رقم 174 لسنة 2025 وقانون العمل الجديد رقم 14 لسنة 2025.",
    ),

    # ── 2. Presidential Decrees & Government Formations ────────────────────────
    "presidential_decrees": TargetSource(
        name="القرارات الجمهورية وقرارات رئيس الجمهورية (2024 - 2026)",
        base_url="https://manshurat.org",
        category="قرار رئاسي",
        term_id=7,
        target_years=[2024, 2025, 2026],
        description="قرارات تشكيل الحكومة، تفويضات رئيس الوزراء، والقرارات الرئاسية السيادية.",
    ),

    # ── 3. Economic, Financial & Investment Regulations ────────────────────────
    "economic_regulations": TargetSource(
        name="اللوائح التنفيذية والتشريعات الاقتصادية والمالية الحديثة",
        base_url="https://manshurat.org",
        category="لائحة",
        term_id=25,
        target_years=[2024, 2025, 2026],
        description="لوائح حماية المنافسة، لوائح قوانين إلغاء الإعفاءات الضريبية للأنشطة الاستثمارية.",
    ),

    # ── 4. Ministerial Decisions & Special Economic Zones ─────────────────────
    "ministerial_decisions": TargetSource(
        name="الوقائع المصرية - القرارات الوزارية والأنشطة الاقتصادية",
        base_url="https://manshurat.org",
        category="قرار وزاري",
        term_id=6,
        target_years=[2024, 2025, 2026],
        description="قرارات المناطق الحرة لقناة السويس، تنظيم الإعلانات، وضوابط الشركات.",
    ),

    # ── 5. International Treaties & Historical Legal Archives ──────────────────
    "international_treaties": TargetSource(
        name="المعاهدات والاتفاقيات الدولية والقوانين التاريخية",
        base_url="https://manshurat.org",
        category="اتفاقية دولية",
        term_id=24,
        target_years=[2017, 2020, 2024, 2025],
        description="الاتفاقيات الاقتصادية الدولية والشراكات الأوروبية ومصادر التشريعات العامة.",
    ),
}


def get_active_sources() -> List[TargetSource]:
    """Returns all active target sources ready for scraping."""
    return list(TARGET_LEGAL_SOURCES.values())
