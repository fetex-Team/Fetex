with open('module4_dispatch/forecast_repositioner.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
idx = text.find('class ForecastRepositioner')
safe = text[idx:idx+3000].encode('ascii', 'replace').decode('ascii')
print(safe)
