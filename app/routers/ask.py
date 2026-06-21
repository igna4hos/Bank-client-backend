import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database import clickhouse_query_with_meta

router = APIRouter(prefix="/ask", tags=["ask"])

_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_DANGEROUS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|SYSTEM)\b", re.IGNORECASE
)

_SCHEMA = """
Ты — аналитик данных банковской платформы. Работаешь с ClickHouse (база bank_marts).

ТАБЛИЦЫ:

bank_marts.daily_turnover — дневные обороты бизнесов (МСБ)
  date Date, business_id UUID,
  inflow_sum Decimal(18,2), outflow_sum Decimal(18,2),
  inflow_count UInt32, outflow_count UInt32, tx_count UInt32, avg_tx_amount Decimal(18,2),
  unique_counterparties UInt32, active_day UInt8, cash_withdrawal_sum Decimal(18,2),
  balance_avg Decimal(18,2), balance_min Decimal(18,2), balance_volatility Decimal(18,2)

bank_marts.monthly_turnover — месячные обороты бизнесов
  month Date, business_id UUID,
  inflow_sum Decimal(18,2), outflow_sum Decimal(18,2),
  tx_count UInt32, avg_balance Decimal(18,2), unique_counterparties UInt32

bank_marts.daily_service_usage — использование сервисов клиентами по дням
  date Date, client_id UUID, service_id UUID,
  session_count UInt32, event_count UInt32, tx_sum Float64, tx_count UInt32, cancel_count UInt32

bank_marts.monthly_service_usage — использование сервисов по месяцам
  month Date, client_id UUID, service_id UUID,
  active_days UInt32, session_count UInt32, tx_sum Float64, unique_services_used UInt32

bank_marts.client_service_baseline — baseline использования сервисов клиентами
  client_id UUID, baseline_period String, metric String,
  mean_value Float64, std_deviation Float64, p25 Float64, p75 Float64, calculated_at DateTime

bank_marts.daily_friction_stats — UX-метрики воронок по дням
  date Date, client_id UUID, funnel_id UUID,
  friction_event_count UInt32, rage_click_count UInt32, idle_count UInt32,
  ui_error_count UInt32, exit_without_action_count UInt32,
  session_count UInt32, completed_session_count UInt32,
  funnel_success_rate Float64, avg_task_duration_sec Float64,
  ux_tickets_count UInt32, is_active_day UInt8

bank_marts.client_friction_baseline — baseline UX-показателей клиентов
  client_id UUID, baseline_period String, metric String,
  mean_value Float64, std_deviation Float64, p25 Float64, p75 Float64, calculated_at DateTime

bank_marts.anomaly_alerts — алёрты об аномалиях (detected_at в UTC, Москва = UTC+3)
  alert_id UUID, detected_at DateTime,
  anomaly_type LowCardinality(String),
  entity_type LowCardinality(String),  -- 'business' или 'client'
  entity_id UUID, metric_name String, metric_value Float64,
  baseline_mean Float64, baseline_std Float64, deviation_sigma Float64,
  severity LowCardinality(String),  -- 'low', 'medium', 'high', 'critical'
  details String, is_resolved UInt8

bank_marts.dim_businesses — справочник бизнесов (МСБ)
  business_id UUID, company_name String, inn String, industry String,
  segment String, region String, tax_regime String, current_tariff String,
  is_active UInt8, synced_at DateTime

bank_marts.dim_clients — справочник клиентов (физлица)
  client_id UUID, full_name String, segment String, region String,
  primary_product String, subscription_plan String, is_active UInt8, synced_at DateTime

bank_marts.dim_services — справочник сервисов экосистемы
  service_id UUID, service_name String, service_type String, is_active UInt8, synced_at DateTime

bank_marts.dim_funnels — справочник воронок (экранов приложения)
  funnel_id UUID, funnel_name String, service_id UUID,
  target_event String, benchmark_duration_sec UInt32, is_active UInt8, synced_at DateTime

СВЯЗИ ДЛЯ JOIN:
  daily_turnover.business_id → dim_businesses.business_id
  daily_service_usage.service_id → dim_services.service_id
  daily_friction_stats.funnel_id → dim_funnels.funnel_id
  dim_funnels.service_id → dim_services.service_id
  anomaly_alerts.entity_id → dim_businesses.business_id (когда entity_type = 'business')
  anomaly_alerts.entity_id → dim_clients.client_id (когда entity_type = 'client')

ПРАВИЛА:
- Верни ТОЛЬКО SQL-запрос, без пояснений, без markdown-блоков (не используй ```)
- Только SELECT запросы
- LIMIT 50, если пользователь не просил иного
- Функции дат ClickHouse: today(), yesterday(), toDate(), toStartOfMonth(), now() - INTERVAL X DAY
""".strip()


class AskRequest(BaseModel):
    question: str


async def _gpt(messages: list[dict], temperature: float) -> str:
    payload = {
        "modelUri": f"gpt://{settings.yandex_folder_id}/{settings.yandex_model}",
        "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": 2000},
        "messages": messages,
    }
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(
            _API_URL,
            json=payload,
            headers={"Authorization": f"Api-Key {settings.yandex_api_key}"},
        )
        r.raise_for_status()
        return r.json()["result"]["alternatives"][0]["message"]["text"].strip()


def _clean_sql(raw: str) -> str:
    return re.sub(r"^```\w*\n?", "", raw).rstrip("`").strip()


async def _generate_sql(question: str) -> str:
    return _clean_sql(await _gpt(
        [{"role": "system", "text": _SCHEMA}, {"role": "user", "text": question}],
        temperature=0.1,
    ))


async def _fix_sql(question: str, bad_sql: str, error: str) -> str:
    return _clean_sql(await _gpt(
        [
            {"role": "system", "text": _SCHEMA},
            {"role": "user", "text": question},
            {"role": "assistant", "text": bad_sql},
            {"role": "user", "text": f"Ошибка: {error}\nИсправь SQL. Верни только запрос."},
        ],
        temperature=0.1,
    ))


async def _generate_answer(question: str, sql: str, columns: list, rows: list) -> str:
    header = " | ".join(columns)
    body = "\n".join(" | ".join(str(v) for v in row) for row in rows[:50])
    note = f"\n(показаны первые 50 из {len(rows)} строк)" if len(rows) > 50 else ""
    return await _gpt(
        [
            {"role": "system", "text": "Ты аналитик банковских данных. Отвечай кратко, по делу, на русском."},
            {
                "role": "user",
                "text": (
                    f"Вопрос: {question}\n\n"
                    f"SQL:\n{sql}\n\n"
                    f"Результат ({len(rows)} строк):{note}\n{header}\n{body}\n\n"
                    f"Дай аналитический ответ на вопрос."
                ),
            },
        ],
        temperature=0.3,
    )


@router.post("/", summary="Вопрос на естественном языке → SQL → аналитический ответ")
async def ask(payload: AskRequest):
    if not settings.yandex_api_key:
        raise HTTPException(status_code=503, detail="YandexGPT не настроен (нет API-ключа)")

    question = payload.question.strip()
    sql = await _generate_sql(question)

    if not _SELECT_RE.match(sql) or _DANGEROUS_RE.search(sql):
        raise HTTPException(status_code=422, detail=f"Модель сгенерировала недопустимый запрос: {sql}")

    result = None
    last_error = ""
    for attempt in range(2):
        try:
            result = await clickhouse_query_with_meta(sql)
            break
        except Exception as e:
            last_error = str(e)
            if attempt == 0:
                sql = await _fix_sql(question, sql, last_error)
                sql = _clean_sql(sql)

    if result is None:
        raise HTTPException(status_code=422, detail=f"Не удалось выполнить запрос: {last_error}")

    rows = result["rows"][:100]
    if not rows:
        return {"answer": "По вашему запросу данных не найдено.", "sql": sql, "rows": 0}

    answer = await _generate_answer(question, sql, result["columns"], rows)
    return {"answer": answer, "sql": sql, "rows": len(rows)}
