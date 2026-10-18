import json
category = parameters['category']
page = 1
total = 0
complete = True
while True:
    try:
        response = tools.call('mock_supplier_list_catalogue', {'category':category,'page':page})
    except RuntimeError:
        complete = False
        break
    if not response['items']: break
    total += len(response['items'])
    page += 1
print(json.dumps({'category':category,'total':total,'complete':complete}))
