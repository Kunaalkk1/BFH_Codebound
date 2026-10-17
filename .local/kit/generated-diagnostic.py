import json
articles = []
page = 1
while True:
    response = tools.call('mock_erp_list_articles', {'status':'active','page':page,'pageSize':100})
    articles.extend(response['items'])
    if page >= response['totalPages']: break
    page += 1
prices = {}
for start in range(0,len(articles),100):
    response = tools.call('mock_supplier_get_prices', {'skus':[a['sku'] for a in articles[start:start+100]]})
    prices.update(response['prices'])
rows = []
for article in articles:
    sku = article['sku']
    if sku not in prices or article['purchasePrice'] <= 0: continue
    drift = (prices[sku]-article['purchasePrice'])/article['purchasePrice']*100
    if drift > parameters['threshold']:
        rows.append({'sku':sku,'purchasePrice':article['purchasePrice'],'supplierPrice':prices[sku],'driftPct':int(drift*10+0.5)/10})
rows.sort(key=lambda row:row['driftPct'],reverse=True)
print(json.dumps({'rows':rows}))