import json
from datetime import datetime, timedelta
cutoff = datetime.fromisoformat(parameters['as_of'].replace('Z','+00:00'))-timedelta(days=parameters['days'])
orders = []
page = 1
while True:
    response = tools.call('mock_erp_list_orders', {'status':'shipped','page':page,'pageSize':50})
    orders.extend(response['items'])
    if page >= response['totalPages']: break
    page += 1
rows = []
for order in orders:
    parcel = tools.call('mock_carrier_get_tracking', {'tracking_no':order['trackingNo']})
    if parcel['status'] != 'delivered' and datetime.fromisoformat(parcel['lastScanAt'].replace('Z','+00:00')) < cutoff:
        rows.append({'orderNo':order['orderNo'],'trackingNo':parcel['trackingNo'],'status':parcel['status'],'lastScanAt':parcel['lastScanAt']})
rows.sort(key=lambda row:row['lastScanAt'])
print(json.dumps({'rows':rows}))
