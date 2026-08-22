import urllib.request, urllib.error
req = urllib.request.Request(
    'https://api-dev.jbengineeringcorporation.online/api/work-order-sales',
    method='OPTIONS',
    headers={
        'Origin': 'http://localhost:8080',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'Content-Type'
    }
)
try:
    resp = urllib.request.urlopen(req)
    print(resp.headers)
except urllib.error.HTTPError as e:
    print(e.headers)
