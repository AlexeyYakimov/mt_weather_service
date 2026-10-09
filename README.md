# Weather cache API

Сервис загружает прогноз из MET Norway сразу при старте и затем каждые 2 часа,
атомарно сохраняет в SQLite только почасовые записи текущего дня и отдаёт их
мобильному клиенту из кэша. Запросы мобильного API никогда не вызывают MET Norway.

## Запуск

```bash
cp .env.example .env
# Задайте надёжный API_TOKEN и настоящий контакт в MET_USER_AGENT
docker compose up --build -d
```

MET Norway требует идентифицируемый `User-Agent`; без него upstream вернёт 403.
SQLite хранится в Docker volume `weather-data` и переживает перезапуск контейнера.

## API

Токен передаётся стандартным Bearer-заголовком:

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  http://localhost:8000/api/v1/forecast/current

curl -H "Authorization: Bearer $API_TOKEN" \
  http://localhost:8000/api/v1/forecast/day
```

- `GET /api/v1/forecast/current` — прогноз на текущий локальный час.
- `GET /api/v1/forecast/day` — весь доступный прогноз на текущую локальную дату.
- `GET /health` — состояние сервиса и готовность кэша, без авторизации.
- `GET /docs` — интерактивная OpenAPI-документация.

Пока первая загрузка не завершилась (либо upstream недоступен и сегодняшнего кэша
ещё нет), forecast-эндпоинты отвечают `503`. При ошибке очередного обновления ранее
полученный сегодняшний кэш остаётся доступным.

## Настройки `.env`

| Переменная | Назначение | Значение по умолчанию |
|---|---|---|
| `API_TOKEN` | Bearer-токен (минимум 16 символов) | обязательна |
| `MET_USER_AGENT` | Идентификатор и контакт для MET Norway | обязательна |
| `LATITUDE` / `LONGITUDE` | Координаты | `55.60` / `37.41` |
| `TIMEZONE` | IANA timezone для границ дня | `Europe/Moscow` |
| `UPDATE_INTERVAL_HOURS` | Интервал обновления | `2` |
| `DATABASE_PATH` | Путь SQLite внутри контейнера | `/data/weather.db` |

Пример структуры ответа сохранён без потери полей MET: каждый объект `forecast`
или элемент `forecasts` содержит исходные `time` и `data` (`instant`,
`next_1_hours`, `next_6_hours`, `next_12_hours`). Единицы измерения находятся в
верхнеуровневом поле `units`.

MET передаёт `time` в UTC, но мобильное API отдаёт только `local_time` с часовым
поясом `Europe/Moscow`; исходное UTC-время остаётся только во внутреннем кэше.
Давление `air_pressure_at_sea_level` преобразуется из гПа в мм рт. ст.,
округляется до одного знака и имеет единицу `mmHg` в `units`. Служебные блоки
`source` и `location` в мобильный ответ не включаются.

Источник и правила использования API: [MET Norway Locationforecast](https://api.met.no/weatherapi/locationforecast/2.0/documentation).
