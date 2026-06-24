"""seed domain dictionary labels

Revision ID: 0015_seed_domain_dict
Revises: 0014_add_domain_dictionary
Create Date: 2026-06-24 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0015_seed_domain_dict"
down_revision: Union[str, None] = "0014_add_domain_dictionary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LABELS = [
    ("00000000-0015-0000-0000-000000000001", "샘플번호", "샘플번호", ["생플번호", "생플변호", "샘플변호", "팸플번호"]),
    ("00000000-0015-0000-0000-000000000002", "사업자번호", "사업자번호", ["사엽자번호", "사엽자변호", "사업자변호"]),
    ("00000000-0015-0000-0000-000000000003", "담당", "담당", ["당당"]),
    ("00000000-0015-0000-0000-000000000004", "매장판매", "매장판매", ["매장판애", "매장판애수"]),
    ("00000000-0015-0000-0000-000000000005", "배달판매", "배달판매", ["배달판마", "배당판매"]),
    ("00000000-0015-0000-0000-000000000006", "공급가액", "공급가액", ["공금가액", "공급가격"]),
    ("00000000-0015-0000-0000-000000000007", "결제합계", "결제합계", ["결재합계"]),
    ("00000000-0015-0000-0000-000000000008", "합계금액", "합계금액", ["합계 금액", "총합계", "총 합계"]),
    ("00000000-0015-0000-0000-000000000009", "부가세", "부가세", ["VAT", "V.A.T", "세액"]),
]


def upgrade() -> None:
    for entry_id, canonical, normalized, aliases in LABELS:
        op.execute(
            f"""
            INSERT INTO domain_dictionary_entries
                (id, dictionary_type, canonical_value, normalized_value, field, source, memo, active)
            VALUES
                ('{entry_id}', 'field_label', '{canonical}', '{normalized}', 'key', 'seed_manufacturing_label_dictionary', '기본 제조업 라벨/오타 사전', TRUE)
            ON CONFLICT (dictionary_type, canonical_value) DO NOTHING
            """
        )
        entry_number = int(entry_id[-12:])
        for index, alias in enumerate(aliases, start=1):
            alias_id = f"00000000-0015-{entry_number:04d}-{index:04d}-000000000000"
            normalized_alias = alias.replace(" ", "").replace(".", "").replace(":", "").replace("/", "").replace("_", "").replace("-", "").lower()
            op.execute(
                f"""
                INSERT INTO domain_dictionary_aliases
                    (id, entry_id, alias_value, normalized_alias_value, source, confidence, active)
                VALUES
                    ('{alias_id}', '{entry_id}', '{alias}', '{normalized_alias}', 'seed_manufacturing_label_dictionary', 1.000, TRUE)
                ON CONFLICT (entry_id, alias_value) DO NOTHING
                """
            )


def downgrade() -> None:
    op.execute("DELETE FROM domain_dictionary_aliases WHERE source = 'seed_manufacturing_label_dictionary'")
    op.execute("DELETE FROM domain_dictionary_entries WHERE source = 'seed_manufacturing_label_dictionary'")
