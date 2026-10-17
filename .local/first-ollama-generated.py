import json
import datetime
import statistics
import math
import collections
import decimal

def calculate_analytics(records, params):
    filtered_records = records
    if params['ticket_type'] is not None:
        filtered_records = [r for r in filtered_records if r['ticket_type'] == params['ticket_type']]
    start_date = datetime.date(params['year'], params['month'], 1)
    end_date = datetime.date(params['year'], params['month'], 30)
    filtered_records = [r for r in filtered_records if start_date <= datetime.date.fromisoformat(r['travel_date']) <= end_date]
    ticket_count = len(filtered_records)
    total_revenue_cents = sum(r['price_cents'] for r in filtered_records)
    average_revenue_cents = statistics.mean(r['price_cents'] for r in filtered_records) if ticket_count > 0 else 0
    result = {'ticket_count': ticket_count, 'total_revenue_cents': total_revenue_cents, 'average_revenue_cents': average_revenue_cents}
    return result

data = json.load(open('/tmp/input.json', encoding='utf-8'))
result = calculate_analytics(data['records'], data['parameters'])
print(json.dumps(result))