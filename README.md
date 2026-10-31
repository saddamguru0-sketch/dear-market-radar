# Dear Market Viral Radar

Roz subah 7:00 AM (IST) ye radar free mein chalta hai:
- 5 reference channels (TSCS, GEN, New Money, Akshat, Rahul Jain) ke last 50 videos scan karta hai.
- Har video ka **outlier score** nikalta hai: video ke views ÷ us channel ke normal (median) views. 2x ya usse zyada = viral breakout.
- 5 Hindi finance keywords pe last 7 din ke top videos dhoondhta hai, aur unka **views/subscriber** ratio nikalta hai (chhote channel pe bade views = topic hot hai).
- Dear Market ke apne videos ka performance bhi track karta hai.

Results `reports/latest.md` (padhne ke liye) aur `data/latest.json` (Claude ke liye) mein save hote hain. Roz ki file `data/history/` mein jaati hai.

Kharcha: ₹0. Roz ka use ~600 units hai, aur Google ki free limit 10,000 units/day hai.

## Setup (ek baar, ~15 minute)

### 1. YouTube API key banao
1. https://console.cloud.google.com kholo aur apne Google account se login karo.
2. Upar **Select a project → New Project** pe click karo, naam do `dear-market-radar` → Create.
3. Search bar mein **YouTube Data API v3** likho → **Enable** dabao.
4. Left menu: **APIs & Services → Credentials → Create credentials → API key**.
5. Key copy kar lo. (Optional: "Restrict key" mein sirf YouTube Data API v3 select kar do.)

Billing ya card ki zaroorat nahi hai.

### 2. GitHub repo banao
1. https://github.com pe account banao ya login karo.
2. **New repository** banao, naam `dear-market-radar` rakho, aur **Public** select karo. Public isliye taaki Claude bina password ke results padh sake. Isme sirf public YouTube stats hain, koi personal data nahi.
3. **Add file → Upload files** pe jao aur is folder ki saari files upload karo, `.github` folder bhi. (`.github` folder hidden ho sakta hai, isliye zip se nikalte waqt dhyan dena.)

### 3. Key ko secret mein daalo
Repo mein **Settings → Secrets and variables → Actions → New repository secret** pe jao.
- Name: `YOUTUBE_API_KEY`
- Value: apni key paste karo → Add secret.

### 4. Pehla test
**Actions** tab kholo → **Viral Radar** → **Run workflow** dabao. 1-2 minute mein hara tick aayega, aur `reports/latest.md` file ban jaayegi.

Agar laal cross aaye, toh error ka screenshot Claude ko bhej do.

### 5. Claude ko repo link bhejo
Repo ka link Claude ko bhejo. Phir roz 7:36 wala "Viral Radar" scheduled task is data ko padhkar breakdown dega.

## Badlav kaise karein
`config.json` file edit karo:
- `referenceChannels`: channel handles add ya remove karo.
- `keywords`: search topics badlo. Har keyword 100 units leta hai, max 5 rakhna theek hai.
- `minOutlier`: 2 = sirf 2x+ wale videos dikhenge.
