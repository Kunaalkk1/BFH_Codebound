import json
import math
response = parameters['response']
items = response['items']
error = None
for index, item in enumerate(items):
    if type(item.get('price_usd')) not in (int,float) or not math.isfinite(item['price_usd']):
        error = {'code':'PROCESSOR_INPUT_INVALID','path':'items['+str(index)+'].price_usd'}
        break
    if not isinstance(item.get('description'),str):
        error = {'code':'PROCESSOR_INPUT_INVALID','path':'items['+str(index)+'].description'}
        break
if error:
    result = {'error':error}
else:
    for item in items:
        item['price_eur'] = math.floor(item['price_usd']*0.92*100+0.5)/100
    for item in items:
        item['price_eur_gross'] = math.floor(item['price_eur']*1.19*100+0.5)/100
    for item in items:
        item['description'] = ' '.join(item['description'].split())
    result = response
print(json.dumps(result))
