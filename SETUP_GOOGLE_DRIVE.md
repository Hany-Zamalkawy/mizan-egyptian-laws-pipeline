# 🚀 دليل إعداد وتشغيل خط أنابيب القوانين المصرية مع Google Drive

هذا الدليل يوضح لك بالخطوات العملية والمبسطة كيفية إعداد خط الأنابيب (Pipeline) على جهازك المحلي، والحصول على ملف `credentials.json` الخاص بـ Google Drive مجانًا وبشكل رسمي، وجدولة التشغيل تلقائيًا.

---

## 📑 الفهرس
1. [تثبيت المتطلبات وبيئة العمل](#1-تثبيت-المتطلبات-وبيئة-العمل)
2. [إنشاء حساب الخدمة وتحميل `credentials.json` من Google Cloud](#2-إنشاء-حساب-الخدمة-وتحميل-credentialsjson-من-google-cloud)
3. [مشاركة مجلد Google Drive مع حساب الخدمة](#3-مشاركة-مجلد-google-drive-مع-حساب-الخدمة)
4. [إعداد المتغيرات في ملف `.env`](#4-إعداد-المتغيرات-في-ملف-env)
5. [تشغيل خط الأنابيب يدويًا](#5-تشغيل-خط-الأنابيب-يدويًا)
6. [أتمتة التشغيل وجدولته (Windows & Linux/macOS)](#6-أتمتة-التشغيل-وجدولته-windows--linuxmacos)

---

## 1. تثبيت المتطلبات وبيئة العمل

افتح موجه الأوامر (PowerShell أو Terminal) في مجلد المشروع، ثم أنشئ بيئة افتراضية وثبّت الحزم:

```bash
# إنشاء بيئة افتراضية
python -m venv venv

# تفعيل البيئة:
# على Windows:
.\venv\Scripts\Activate.ps1
# على Linux / macOS:
source venv/bin/activate

# تثبيت الحزم المطلوبة
pip install -r requirements_local.txt
```

---

## 2. إنشاء حساب الخدمة وتحميل `credentials.json` من Google Cloud

Google Drive API مجانية تمامًا. لربط البايثون بحسابك:

1. افتح **[Google Cloud Console](https://console.cloud.google.com/)**.
2. أنشئ مشروعًا جديدًا بالضغط على **Select a Project** أعلى الشاشة ثم **New Project** (مثلاً سمّه `Egypt-Legal-Pipeline`).
3. فعّل **Google Drive API**:
   - من القائمة الجانبية ☰ انتقل إلى **APIs & Services** > **Library**.
   - ابحث عن **Google Drive API** واضغط **Enable**.
4. أنشئ حساب خدمة (Service Account):
   - انتقل إلى **APIs & Services** > **Credentials**.
   - اضغط على **+ CREATE CREDENTIALS** في الأعلى واختر **Service account**.
   - أدخل اسمًا للحساب (مثلاً: `drive-pipeline-bot`) واضغط **Create and Continue** ثم **Done**.
5. استخراج وتحميل مفتاح الـ JSON:
   - في صفحة **Credentials**، ستجد الحساب الذي تم إنشاؤه تحت قسم **Service Accounts**.
   - اضغط على الإيميل الخاص به (ينتهي بـ `@...iam.gserviceaccount.com`).
   - اذهب إلى تبويب **Keys** في الأعلى.
   - اضغط **Add Key** > **Create new key**.
   - اختر نوع المفتاح **JSON** ثم اضغط **Create**.
   - سيتم تحميل ملف تلقائيًا على جهازك. **أعد تسمية هذا الملف إلى `credentials.json` وضعه داخل مجلد المشروع الرئيسي**.

---

## 3. مشاركة مجلد Google Drive مع حساب الخدمة

> ⚠️ **خطوة حاسمة**: حساب الخدمة له مساحة افتراضية خاصة به، لكي تظهر الملفات في حسابك الشخصي أو مجلد فريقك المشترك، يجب مشاركة المجلد معه:

1. افتح [Google Drive](https://drive.google.com/) وأنشئ مجلدين جديدين:
   - مجلد للملفات الخام (PDFs/Texts): مثلاً باسم `Egypt_Laws_Raw`.
   - مجلد للبيانات المهيكلة (Excel/JSON): مثلاً باسم `Egypt_Laws_Processed`.
2. انسخ عنوان بريد حساب الخدمة (الموجود داخل ملف `credentials.json` في حقل `"client_email"`).
3. في Google Drive، اضغط كليك يمين على كل مجلد > **Share** (مشاركة).
4. الصق إيميل حساب الخدمة واجعل صلاحيته **Editor** (محرر) وتأكد من إلغاء تحديد خيار "إشعار الأشخاص" ثم اضغط **Share**.
5. احصل على **Folder ID** لكل مجلد من شريط العنوان (URL):
   - رابط المجلد يكون بالشكل التالي:
     `https://drive.google.com/drive/folders/1aBcD_EFGhIJkLMNoPQRstuvwxyz`
   - المعرف (ID) هو الجزء الأخير: `1aBcD_EFGhIJkLMNoPQRstuvwxyz`.

---

## 4. إعداد المتغيرات في ملف `.env`

قم بإنشاء ملف باسم `.env` بجانب ملف `main.py` وضع فيه الآتي:

```env
# مسار ملف المفتاح
GOOGLE_APPLICATION_CREDENTIALS=credentials.json

# معرفات المجلدات التي نسختها من الرابط في الخطوة السابقة
GDRIVE_RAW_FOLDER_ID=ضع_هنا_معرف_مجلد_الـ_RAW
GDRIVE_PROCESSED_FOLDER_ID=ضع_هنا_معرف_مجلد_الـ_PROCESSED
```

---

## 5. تشغيل خط الأنابيب يدويًا

### تشغيل كامل (Scraping + Parsing + Drive Upload):
```bash
python main.py --limit 5 --pages 1
```

### تشغيل محلي فقط للتجربة (بدون رفع على Drive):
```bash
python main.py --limit 3 --skip-upload
```

### هيكل المخرجات المحلية:
- `./raw_data/` : يحتوي على ملفات الـ PDF الأصلية وملفات النصوص الخام.
- `./processed_data/` :
  - `laws_and_articles_YYYYMMDD_HHMMSS.xlsx` : مصنف Excel منظم يحتوي على ورقتين (ورقة للقوانين وورقة للمواد).
  - `laws_structured_YYYYMMDD_HHMMSS.json` : ملف JSON متكامل مهيكل لجميع القوانين والمواد.
  - `laws_summary_YYYYMMDD_HHMMSS.csv` : ملف CSV ملخص.

---

## 6. أتمتة التشغيل وجدولته (Windows & Linux/macOS)

### 🪟 على Windows عبر Task Scheduler

يمكنك جدولة تشغيل السكريبت يوميًا في وقت محدد (مثلاً الساعة 3:00 فجرًا):

#### الطريقة السريعة عبر PowerShell (بصلاحيات المسؤول):
افتح PowerShell وشغّل الأمر التالي (مع تعديل المسار لمجلد مشروعك إذا لزم):

```powershell
$Action = New-ScheduledTaskAction -Execute "$PWD\venv\Scripts\python.exe" -Argument "$PWD\main.py --limit 10" -WorkingDirectory "$PWD"
$Trigger = New-ScheduledTaskTrigger -Daily -At 3:00AM
Register-ScheduledTask -TaskName "EgyptLawsDailyIngestion" -Action $Action -Trigger $Trigger -Description "Daily scraping and Google Drive syncing for Egyptian laws."
```

#### الطريقة المرئية عبر واجهة الويندوز:
1. اضغط `Win + R` واكتب `taskschd.msc` واضغط Enter.
2. اضغط **Create Basic Task** من القائمة اليمنى.
3. اكتب الاسم: `EgyptLawsDailyIngestion` ثم اضغط Next.
4. اختر **Daily** وحدد التوقيت المناسب (مثلاً 03:00:00 AM).
5. في خطوة Action اختر **Start a program**:
   - **Program/script**: مسار `python.exe` داخل البيئة الافتراضية، مثلاً:
     `D:\Hany_Zamlkawy\study\Interships\DEPI\Technical Seasions\Final Project\ميزان\venv\Scripts\python.exe`
   - **Add arguments**: `main.py --limit 10`
   - **Start in**: مسار مجلد المشروع:
     `D:\Hany_Zamlkawy\study\Interships\DEPI\Technical Seasions\Final Project\ميزان`
6. اضغط Finish.

---

### 🐧 على Linux / macOS عبر Cron

1. افتح محرر الـ crontab:
   ```bash
   crontab -e
   ```
2. أضف السطر التالي للتشغيل يوميًا الساعة 03:00 صباحًا:
   ```bash
   0 3 * * * cd "/path/to/project" && ./venv/bin/python main.py --limit 10 >> cron_pipeline.log 2>&1
   ```
3. احفظ الملف واخرج (`Ctrl + X` ثم `Y` في nano).
