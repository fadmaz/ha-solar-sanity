/** Shared card and editor copy. Entity IDs and provider names are never translated. */
export type UiLanguage = "ar" | "en";
export type LanguageSetting = "auto" | UiLanguage;

export function languageOf(language?: string, setting?: unknown): UiLanguage {
  if (setting === "ar" || setting === "en") return setting;
  return language?.toLowerCase().startsWith("ar") ? "ar" : "en";
}

export function validateLanguage(setting: unknown): void {
  if (setting !== undefined && !["auto", "ar", "en"].includes(String(setting))) {
    throw new Error("language must be auto, ar or en");
  }
}

export function number(value: number, language: UiLanguage = "en", digits = 0): string {
  return new Intl.NumberFormat(language, {
    numberingSystem: language === "ar" ? "arab" : "latn",
    useGrouping: false, minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(value);
}

const AR: Record<string, string> = {
  "min": "د",
  "no_graded_samples": "لم تكتمل ملاحظات قابلة للتقييم بعد؛ يستمر جمع البيانات.",
  "no_full": "لا يصل الشحن إلى الهدف ضمن أفق التوقع.",
  "no_takeover": "لا تغطي الشمس الحمل ضمن أفق التوقع.",
  "needs_forecast": "يلزم توقع شمسي يغطي فترة الشمس القادمة.",
  "reserve_not_reached": "تكفي البطارية حتى تتولى الشمس الحمل.",
  "Connecting": "جارٍ الاتصال",
  "Waiting for Home Assistant.": "بانتظار Home Assistant.",
  "Home Assistant is still starting": "Home Assistant ما زال يبدأ التشغيل",
  "This will fill in by itself.": "ستظهر البيانات تلقائياً بعد اكتمال التشغيل.",
  "Installation not found": "التركيب غير موجود",
  "The device this card was set to show no longer exists. Choose another in the card's settings.":
    "الجهاز المحدد لهذه البطاقة لم يعد موجوداً. اختر جهازاً آخر من إعدادات البطاقة.",
  "Solar Sanity is not set up yet": "لم يتم إعداد Solar Sanity بعد",
  "Add it under Settings, Devices & services.": "أضفه من الإعدادات، ثم الأجهزة والخدمات.",
  "More than one installation": "يوجد أكثر من تركيب",
  "This card found {n}. Choose one in the card's settings.":
    "وجدت البطاقة {n} تركيبات. اختر واحداً من إعداداتها.",
  "The battery's charge level is not reporting": "قراءة شحن البطارية غير متاحة",
  "The outlook comes back as soon as it does.": "يعود التوقع تلقائياً عند عودة القراءة.",
  "Battery outlook": "توقع البطارية",
  "The battery outlook is off": "توقع البطارية غير مفعّل",
  "Battery outlook is off": "توقع البطارية غير مفعّل",
  "Map the battery's charge level and enter its capacity in Solar Sanity, Configure.":
    "اختر حساس شحن البطارية وأدخل سعتها من إعدادات Solar Sanity.",
  "Battery now": "شحن البطارية الآن",
  "Power now": "القدرة الآن",
  "Solar": "الشمس", "House": "البيت", "Battery": "البطارية", "Grid": "الشبكة",
  "idle": "ساكنة", "charging": "تشحن", "discharging": "تفرّغ",
  "today": "اليوم", "tomorrow": "غداً", "Today": "اليوم", "Tomorrow": "غداً",
  "Forecast": "التوقع", "Reserve": "الاحتياطي", "Target": "الهدف",
  "{n} min": "{n} د", "{n} h": "{n} س", "{h} h {m} min": "{h} س {m} د",
  "in {duration}": "بعد {duration}", "In {duration}": "بعد {duration}",
  "the sun takes over at {time}": "تغطي الشمس الحمل عند {time}",
  "Reaches the reserve at {time}": "تصل إلى الاحتياطي عند {time}",
  "Lasts until the sun, at {time}": "تكفي حتى تتولى الشمس الحمل عند {time}",
  "Lowest {pct}{at}": "أقل شحن {pct}{at}", " at {time}": " عند {time}",
  "Full at {time}": "تمتلئ عند {time}", "Full": "ممتلئة", "Since {time}": "منذ {time}",
  "Will not fill today": "لن تمتلئ اليوم",
  "Peaks at {pct} at {time}": "أعلى شحن {pct} عند {time}",
  "Lowest tonight {pct}": "أقل شحن الليلة {pct}", "At {time}": "عند {time}",
  "Battery at {pct}": "شحن البطارية {pct}",
  "Safe load tonight": "الحمل الآمن الليلة", "Lowest before sunrise": "أقل شحن قبل الشمس",
  "Spare sun": "طاقة شمس فائضة", "Usable now": "الطاقة المتاحة الآن",
  "Tonight's use": "استهلاك الليلة", "Spare sun now": "قدرة شمس فائضة الآن",
  "until {time}": "حتى {time}", "at {time}": "عند {time}", "above {pct}": "فوق {pct}",
  "Projected battery charge": "مسار شحن البطارية المتوقع",
  "Projected battery charge from {first}% now to {last}% at {time}":
    "شحن البطارية المتوقع من {first}% الآن إلى {last}% عند {time}",
  "Cannot read the record": "تعذرت قراءة السجل",
  "Solar Sanity keeps its forecast history in Home Assistant's long-term statistics, and they are not available right now.":
    "سجل التوقع محفوظ في إحصاءات Home Assistant طويلة المدى، وهي غير متاحة حالياً.",
  "Reading the record": "جارٍ قراءة السجل", "One moment.": "لحظة واحدة.",
  "No solar forecast to keep": "لا يوجد توقع شمسي لتسجيله",
  "Add Forecast.Solar, Solcast or Open-Meteo, then pick it in Solar Sanity. It will record what they predict, which Home Assistant otherwise throws away within about ten days.":
    "أضف Forecast.Solar أو Solcast أو Open-Meteo واختره في Solar Sanity لحفظ توقعاته قبل حذف سجلها من Home Assistant.",
  "No forecast for tomorrow yet": "توقع الغد لم يصل بعد",
  "Solar Sanity records each provider's forecast every half hour, so tomorrow appears with the next one the provider publishes.":
    "يُسجّل توقع المزود كل نصف ساعة. يظهر الغد مع التوقع التالي الذي ينشره المزود.",
  "Latest forecast, revised through the day. {correction}The day-ahead forecast is kept separately for comparison.":
    "أحدث توقع، يتغير خلال اليوم. {correction}يُحفظ توقع اليوم السابق منفصلاً للمقارنة بالفعلي.",
  "Corrected for your roof. ": "مصحّح لما ينتجه سقفك. ",
  "{provider}: {energy} kilowatt hours {what} for {day}":
    "{provider}: {energy} كيلوواط ساعة {what} ليوم {day}",
  "expected from your roof": "متوقعة من سقفك", "forecast": "حسب التوقع",
  "As the provider forecasts it, not yet corrected for your roof.":
    "كما يتوقعها المزود؛ لم يُتعلّم تصحيح سقفك بعد.",
  "Corrected for your roof: on days forecast like this one, it has delivered about {share}% of the forecast{over}.":
    "مصحّح لسقفك: في الأيام ذات توقع مشابه، أنتج سقفك نحو {share}% من التوقع{over}.",
  "Corrected for your roof: it has delivered about {share}% of this forecast{over}.":
    "مصحّح لسقفك: أنتج سقفك نحو {share}% من هذا التوقع{over}.",
  " over the last {days} days": " خلال آخر {days} أيام",
  "Installation": "التركيب", "Forecast provider": "مزود التوقع", "Language": "اللغة",
  "Automatic": "تلقائي", "Arabic": "العربية", "English": "الإنجليزية",
  "Leave empty to use the only one with a battery outlook.":
    "اتركه فارغاً لاختيار التركيب الوحيد الذي لديه توقع بطارية.",
  "Leave empty to show every provider you have configured.": "اتركه فارغاً لعرض جميع المزودين المحددين.",
  "No day-ahead archive has been written yet. Solar Sanity starts one the first time it records a forecast, which is within a day of you configuring a provider.":
    "لم يُكتب أرشيف اليوم السابق بعد. يبدأ عند أول تسجيل لتوقع بعد إعداد المزود.",
  "Showing {provider}, the only provider archiving so far.": "يُعرض {provider}، وهو المزود الوحيد المؤرشف حتى الآن.",
  "Showing all {n} providers side by side. Choose one to narrow it.":
    "يُعرض المزودون وعددهم {n} جنباً إلى جنب. اختر واحداً لتخصيص العرض.",
  "No installation has a battery outlook yet. Map the battery's charge level and enter its capacity in Solar Sanity, Configure.":
    "لا يوجد تركيب لديه توقع بطارية بعد. اختر حساس الشحن وأدخل السعة من إعدادات Solar Sanity.",
  "Showing the only installation with a battery outlook, found automatically.":
    "يُعرض تلقائياً التركيب الوحيد الذي لديه توقع بطارية.",
  "{n} installations have a battery outlook. Choose one: the card will not pick for you.":
    "يوجد {n} تركيبات لديها توقع بطارية. اختر التركيب الذي تريد عرضه.",
  "Accuracy and data quality": "الدقة وجودة البيانات",
  "Last 28 days": "آخر ٢٨ يوماً", "Last 7 days": "آخر ٧ أيام",
  "Night minimum error": "خطأ أقل شحن", "Reserve calls": "قرارات الاحتياطي",
  "Morning fill calls": "قرارات الامتلاء", "Fill time error": "خطأ وقت الامتلاء",
  "{n} nights · {m} mornings": "{n} ليلة · {m} صباح",
  "{n} samples": "{n} عينة", "{n} excluded · {m} pending": "{n} مستبعدة · {m} قيد الانتظار",
  "percentage points": "نقطة مئوية", "No graded observations yet.": "لم تكتمل ملاحظات قابلة للتقييم بعد.",
  "Last capture": "آخر التقاط", "Archive verified": "آخر تحقق من الأرشيف",
  "Starting": "بانتظار البيانات", "OK": "التجميع سليم", "Degraded": "بيانات غير مكتملة", "Error": "تعذر التجميع",
  "waiting_for_checkpoint": "بانتظار موعد التسجيل عند 08:00 أو20:00 بتوقيت البيت.",
  "waiting_for_capture": "بانتظار أول توقع صالح من المزود.",
  "archive_pending": "أُرسلت البيانات للمسجّل وبانتظار التحقق من الأرشيف.",
  "archive_unverified": "تعذر تأكيد محتوى آخر دفعة في الأرشيف.",
  "archive_not_submitted": "تعذر إرسال دفعة الأرشيف إلى المسجّل.",
  "capture_unavailable": "لم يُرجع آخر التقاط بيانات توقع صالحة.",
  "channel_unreadable": "إحدى قنوات القياس غير متاحة أو لا تحمل تصنيفاً مدعوماً.",
  "forecast_stale": "لم يُلتقط توقع جديد خلال ثلاث ساعات.",
  "journal_restore_failed": "تعذرت قراءة سجل التوقعات؛ حُفظ ملفه دون تعديل.",
  "journal_write_failed": "تعذر حفظ التوقع الصادر.",
  "journal_update_failed": "تعذر تحديث السجل؛ توقع البطارية ما زال يعمل.",
  "soc_unavailable": "حساس شحن البطارية لا يقدّم قراءة رقمية.",
  "soc_stale": "قراءة الشحن قديمة مع حركة بطارية أو دون بيانات كافية لإثبات سكونها.",
  "learning_consumption": "جارٍ تعلّم استهلاك البيت لكل ساعة؛ يلزم نحو ثلاثة أيام من التاريخ.",
  "no_production_expectation": "لا يوجد توقع شمسي أو تاريخ إنتاج كافٍ لتقدير عودة الشمس.",
  "no_horizon": "لا توجد فترة قادمة يمكن إسقاط الشحن عليها.",
  "no_night": "لا تقع ليلة ضمن أفق التوقع.",
};

export function t(language: UiLanguage | undefined, source: string,
  values: Record<string, string | number> = {}): string {
  const message = language === "ar" ? (AR[source] ?? source) : source;
  return message.replace(/\{(\w+)\}/g, (token, key: string) => String(values[key] ?? token));
}

export function reasonText(language: UiLanguage, code: unknown, fallback: string): string {
  return language === "ar" && typeof code === "string" && AR[code] ? AR[code]! : fallback;
}
