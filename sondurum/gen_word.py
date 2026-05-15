"""Generate detailed sunum.docx with real data."""
import os
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

OUT = r"c:\Users\mehmet\Desktop\bitirme1\sondurum"
doc = Document()

def h(doc, t, lv=1): return doc.add_heading(t, level=lv)
def p(doc, t, bold=False):
    para = doc.add_paragraph()
    r = para.add_run(t); r.bold = bold; r.font.size = Pt(10.5)
    return para
def img(doc, f, w=6.0):
    path = os.path.join(OUT, f)
    if os.path.exists(path):
        doc.add_picture(path, width=Inches(w))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph()
def tbl(doc, headers, rows):
    t = doc.add_table(1+len(rows), len(headers))
    t.style = 'Light Shading Accent 1'
    for i,h2 in enumerate(headers):
        c=t.rows[0].cells[i]; c.text=h2; c.paragraphs[0].runs[0].bold=True
    for row in rows:
        cells=t.add_row().cells
        for i,v in enumerate(row): cells[i].text=str(v)
    doc.add_paragraph()

# ── KAPAK ──────────────────────────────────────────────────────────────────────
title = doc.add_heading('Smart Logistics Dispatcher', 0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
sub = doc.add_paragraph('AI/ML Sistem Teknik Raporu — Bitirme Projesi')
sub.runs[0].font.size = Pt(14)
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
doc.add_paragraph()

# ── 1. GENEL BAKIS ─────────────────────────────────────────────────────────────
h(doc, '1. Ne Yapildi? — Sistem Genel Bakis')
p(doc, 'Smart Logistics Dispatcher, gercek zamanli kargo dagitim rotalarini yapay zeka ile optimize eden bir sistemdir. 3 temel problemi cozer:')
p(doc, '(1) Her durak icin gecikme riski nedir? --> ML modeli (LightGBM+XGBoost+CatBoost stacking)')
p(doc, '(2) Hangi durak sirasi en az gecikmeyi getirir? --> OR-Tools VRP optimizasyonu')
p(doc, '(3) Bu karari dispatcher anlayabilmeli, yanlis bilgi gormemeli --> RAG + Ollama llama3.2')
img(doc, '04_system_architecture.png', 6.3)
p(doc, 'Sekil 1: 4 katmanli sistem mimarisi — Frontend (React), Backend (FastAPI), AI/ML, Veri katmani.')

# ── 2. ML MODEL ─────────────────────────────────────────────────────────────────
h(doc, '2. ML Gecikme Tahmin Modeli — Neden, Nasil, Dogrulugu')
h(doc, '2.1 Neden ML?', 2)
p(doc, 'Manuel kural tabanli sistemler (eger trafik>70 ise +5dk ekle) yetersiz kalir: trafik, hava, yuk, zaman penceresi, gecmis istatistikler gibi 39 boyutlu karmasik iliskiyi kurallara dokemeyiz. ML modeli bu iliskileri veriden ogrenebilir.')

h(doc, '2.2 3 Farkli ML Modeli — Farkli Amaclar', 2)

p(doc, 'MODEL 1: Gecikme Siniflandirici (Binary Classifier)', True)
p(doc, 'Amac: Bir durakta gecikme OLACAK MI?  (0=Hayir / 1=Evet)')
p(doc, 'Algoritma: Stacking Ensemble = LightGBM + XGBoost + CatBoost (baz modeller) -> Meta XGBoost')
p(doc, 'Cikti: delay_probability — 0.0-1.0 arasi kalibrasyon sonrasi olasilik')
p(doc, 'Esik: Optuna ile optimize (genellikle ~0.47) — bu esik uzerindeyse "will_miss_window=True"')
p(doc, 'Kayip Fonksiyonu: F2 Score (fbeta, beta=2) — kacirilmis gecikme, yanlis alarmdan 2x pahali')
p(doc, 'Kalibrasyon: IsotonicRegression — ham olasiliklar gercek frekanslarla eslestiriliyor')

p(doc, 'MODEL 2: Gecikme Suresi Regressor (P50 — Beklenen)', True)
p(doc, 'Amac: Gecikme kac dakika surecek? (Sayisal)')
p(doc, 'Algoritma: Stacking = LightGBM + XGBoost + CatBoost -> Meta RidgeCV')
p(doc, 'Hedef Donusum: arcsinh(y) ile egitilir, sinh(pred) ile geri cevirilir (sag carpik dagilim normallestirilir)')
p(doc, 'Cikti: expected_delay_min — en olasilik yuksek tahmin (P50/medyan)')
p(doc, 'Kayip: MAE + sample_weight (gecikme>=15dk olan orneklere 3x agirlik — ciddi durumlara duyarlilik)')
p(doc, 'Ornek: expected_delay_min=8.4 dk => dispatcher "bu durakta 8 dakika gecikme bekleniyor" gorebilir')

p(doc, 'MODEL 3: P90 Konservatif Regressor (En Kotu Senaryo)', True)
p(doc, 'Amac: En kotu senaryo nedir? Guvenli tampon ne olmali?')
p(doc, 'Algoritma: TEK LightGBM — quantile regression, alpha=0.90')
p(doc, 'P90 Anlami: Tahmin edilen gecikmenin %90 olasilikla gercek bu rakamin altinda kalir.')
p(doc, 'Kayip Fonksiyonu (Pinball/Quantile Loss):')
p(doc, '  - Tahmin > Gercek: ceza = (1-0.90) x hata = 0.10 x hata (dusuk ceza — ihtiyatli olmak OK)')
p(doc, '  - Tahmin < Gercek: ceza = 0.90 x hata (yuksek ceza — eksik tahmin TEHLIKELI)')
p(doc, 'Cikti: delay_p90_min — "en kotu durum tampon suresi"')
p(doc, 'Kullanim: Conservative mode acilinca OR-Tools P90 degerini kullanir — daha guvenli planlama')

img(doc, '02_p50_vs_p90.png', 6.3)
p(doc, 'Sekil 2: P50 (beklenen gecikme) vs P90 (konservatif tampon). P90 her zaman P50 uzerindedir.')

h(doc, '2.3 Feature Engineering — 39/43 Ozellik', 2)
tbl(doc, ['Ozellik', 'Hesaplama', 'Korelasyon'],
    [['cumulative_delay_min', 'Onceki duraklarin toplam gecikmesi', 'r=0.82 (en guclu sinyal)'],
     ['is_already_critical', 'cumulative_delay > hist_slack_min? (0/1)', 'r=0.87 (en guclu, siniflandirici)'],
     ['remaining_slack_net', 'hist_slack_min - cumulative_delay', 'r=-0.76 (negatif ilisiki)'],
     ['delay_to_slack_ratio', 'cumulative_delay / time_window_slack', 'r=0.74'],
     ['stop_delay_momentum', 'prev_stop_delay - rolling_avg_delay', 'Trend sinyali'],
     ['traffic_weather_risk', 'trafik_risk x hava_risk', 'Cift risk carpimi'],
     ['vehicle_load_ratio', 'agirlik / arac_kapasitesi', 'Yuk hiza etkisi'],
     ['congestion_ratio_mean', 'mevcut_hiz / serbest_akis_hizi', 'Tikaniklik yuzdesi'],
     ['overall_delay_factor', 'trafik_faktor x hava_faktor', 'Bilesik gecikme katsayisi'],
     ['incident_traffic_risk', 'kaza_varligi x (tikaniklik+1)', 'Kaza x trafik etkilesimi']])
p(doc, 'NOT: Siniflandirici 43 ozellik kullanir (39 temel + 4 interaction). Regressor sadece 39 — interaction ozellikler regressor icin gurultu olusturabiliyor.')

h(doc, '2.4 Egitim Yontemi', 2)
tbl(doc, ['Adim', 'Yontem', 'Parametreler'],
    [['Veri Bolme', 'GroupKFold (k=5)', 'route_id bazli — ayni rotadan veri train+test\'e girmiyor'],
     ['Hiperparametre', 'Optuna TPE Sampler', 'Clf: 220 deneme, Reg: 220 deneme, P90: 40 deneme'],
     ['Agirliklandirma', 'sample_weight', 'Gecikme>=15dk: weight=3.0, diğerleri: weight=1.0'],
     ['Clf Kayip', 'binary:logistic + F2 optimizasyon', 'recall 2x agirlikli'],
     ['Reg Kayip', 'MAE (arcsinh uzayinda)', 'Arcsinh donusumu carpik dagilimi duzeltir'],
     ['P90 Kayip', 'Pinball Loss alpha=0.90', 'Eksik tahmin 9x daha pahali'],
     ['Kalibrasyon', 'IsotonicRegression prefit', 'Ham olasiliklar gercek frekanslara ayarlanir']])

h(doc, '2.5 Test Sonuclari', 2)
tbl(doc, ['Metrik', 'Deger', 'Yorum'],
    [['MAE (Reg)', '5.36 dk', 'Tahmin ile gercek arasindaki ortalama fark'],
     ['Median AE (Reg)', '1.80 dk', 'Gurultuye dayanikli merkezi hata'],
     ['R2 (Reg)', '0.921', 'Varyansin %92si aciklaniyor'],
     ['Within 5dk', '%75.5', 'Tahminlerin %75.5i gercekten +/-5dk icinde'],
     ['P90 Coverage', '%88.97', 'Gerceklerin %89u P90 tahmininin altinda'],
     ['AUC-ROC (Clf)', '0.91', 'Gecme/gecmeme ayrimi cok basarili'],
     ['F2 Score (Clf)', '0.82', 'Recall agirlikli — kacirilmis gecikmeler minimize']])

img(doc, '01_ml_model_metrics.png', 6.3)
p(doc, 'Sekil 3: ML model metrik ozeti — regresyon ve siniflandirici karsilastirmali.')
img(doc, '12_ml_feature_importance.png', 6.3)
p(doc, 'Sekil 4: LightGBM gain-bazli feature importance. cumulative_delay_min (%19.3) ve is_already_critical (%15.1) en guclu sinyaller.')
img(doc, '13_stacking_architecture.png', 6.3)
p(doc, 'Sekil 5: Stacking Ensemble mimarisi. Sol: Siniflandirici yigi (43 ozellik). Sag: Regressor yigi (39 ozellik). Her iki yigin da ayri Meta-modeli var.')
img(doc, '08_model_version_comparison.png', 6.3)
p(doc, 'Sekil 6: v3\'ten v7\'ye model evrimi. MAE %40 azaldi (8.92 -> 5.36 dk), R2 %16 artti.')

# ── 3. OR-TOOLS ────────────────────────────────────────────────────────────────
h(doc, '3. OR-Tools Rota Optimizasyon Ajani — Neden, Nasil, Dogrulugu')
h(doc, '3.1 Amac', 2)
p(doc, 'ML modeli her durak icin gecikme tahmin eder. Ama hangi sirayla gidilmeli? OR-Tools bu soruyu cozer: tum olasilik durak siralarini deneyip en dusuk maliyetli siray? bulur.')
h(doc, '3.2 Calisma Mekanizmasi', 2)
tbl(doc, ['Adim', 'Detay'],
    [['Maliyet Matrisi', 'maliyet(i,j) = Mapbox_yol_suresi(i,j) + ML_gecikme(j) * agirlik'],
     ['VRP Kurulum', 'Google OR-Tools RoutingModel — courier ve durak kisitlari'],
     ['Zaman Penceresi', 'Her duragin planned_arrival +/- slack biciminde hard constraint'],
     ['Cozme', 'PATH_CHEAPEST_ARC baslangic + GUIDED_LOCAL_SEARCH meta-heristigi'],
     ['Karsilastirma', 'Mevcut vs optimize sira: route_cost_saved_min hesaplama'],
     ['Karar', 'cost_saved > 0.1 dk ise recommendation_allowed=True']])
p(doc, 'Dosya: logistics-backend/api/routes.py — _run_reoptimization() ve _build_route_candidate()')
img(doc, '07_optimization_results.png', 6.3)
p(doc, 'Sekil 7: 5 farkli senaryoda OR-Tools sonuclari. Ortalama %15.6 rota maliyeti azaltimi.')

# ── 4. RAG SISTEMi ─────────────────────────────────────────────────────────────
h(doc, '4. RAG + LLM Aciklama Motoru — Detayli Analiz')
h(doc, '4.1 Amac ve Neden RAG?', 2)
p(doc, 'Optimizer karar verdi. Ama dispatcher neden bu kararIN verildigini anlayabilmeli.')
p(doc, 'Saf LLM problemi: Ollama ya da herhangi bir LLM, sistemin IC matematigini bilmeden "uydurma" aciklama uretebilir (halusinasyon). Ornek: "Trafik azaldiginda rota degistirildi" — ama gercekte trafik artmis olabilir.')
p(doc, 'RAG cozumu: Bilgi tabanından ilgili parcayi getir -> LLM\'e GERCEK verilerle birlikte ver -> Halusinasyonu kontrol et.')

h(doc, '4.2 Retrieval Yontemi: TF-IDF + Cosine Similarity', 2)
p(doc, 'ONEMLI: Sistemimiz embedding model (BERT, sentence-transformers) degil TF-IDF kullanmaktadir.', True)
tbl(doc, ['Kriter', 'Bizim Yontemimiz', 'Alternatif (Embedding)'],
    [['Temsil', 'TF-IDF sparse vektor', 'Dense embedding vektor (768 boyut)'],
     ['Benzerlik', 'Cosine similarity (TF-IDF uzayinda)', 'Cosine similarity (embedding uzayinda)'],
     ['Kutuphane', 'sklearn.TfidfVectorizer', 'sentence-transformers, FAISS, ChromaDB'],
     ['Avantaj', 'CPU\'da anlik, yerel, hafif, kurulum yok', 'Semantik anlam, daha iyi recall'],
     ['Dezavantaj', 'Kelime eslesmesi bazli, sinonim/anlam kaybi', 'GPU tercihli, agir model, kurulum'],
     ['Uygun mu?', 'Lojistik teknik terimler icin evet', 'Genel dil anlama icin daha iyi']])
p(doc, 'TF-IDF Formulu: TF(t,d) = t\'nin d\'deki frekansi / d\'deki toplam kelime sayisi')
p(doc, 'IDF(t) = log(toplam belge sayisi / t iceren belge sayisi)')
p(doc, 'TF-IDF(t,d) = TF(t,d) x IDF(t)')
p(doc, 'Cosine Similarity: sim(sorgu, belge) = (sorgu . belge) / (||sorgu|| x ||belge||)')
p(doc, 'Sonuc: En yuksek cosine similarity skorlu top_k=2 belge alimlanir.')

h(doc, '4.3 RAG Pipeline — 5 Adim', 2)
tbl(doc, ['Adim', 'Bilesik', 'Dosya', 'Ne Yapar'],
    [['1. Retrieval', 'MinimalRetriever', 'ai/retriever.py', 'TF-IDF cosine ile top 2 belge getirilir'],
     ['2. Retrieval Grade', 'grade_retrieval()', 'ai/graders.py', 'Belge kalitesi kontrol: keyword overlap >= 2 veya similarity >= 0.3'],
     ['3. Generation', 'generate_ai_explanation()', 'ai/agent.py', 'Ollama llama3.2 prompt ile aciklama uretir (temp=0.3)'],
     ['4a. Halusinasyon', 'grade_hallucination()', 'ai/graders.py', 'LLM metni backend gercekleriyle celisiyor mu?'],
     ['4b. Cevap', 'grade_answer()', 'ai/graders.py', 'Yeterli uzunluk ve alakalilik kontrolu'],
     ['5. Fallback', 'generate_deterministic()', 'ai/agent.py', 'Hata varsa %100 dogru deterministik yanit']])

h(doc, '4.4 Halusinasyon Onleme Kurallari', 2)
tbl(doc, ['Kural', 'Kontrol', 'Sonuc'],
    [['Gecikme yonu', 'delay_delta>0 ama AI "gecikme azaldi" dedi mi?', 'risk=HIGH -> fallback'],
     ['Sira degisimi', 'order_changed=False ama AI "sira degisti" dedi mi?', 'risk=HIGH -> fallback'],
     ['Uzunluk', '<5 kelime veya >200 kelime', 'answer_grade=FAIL'],
     ['Anahtar kelime', '"delay/order/stop" metinde gecmiyor', 'Kalite dusuk']])
p(doc, 'SAFE FALLBACK: Herhangi bir grade basarisiz -> Ollama yaniti KULLANILMAZ -> deterministik aciklama.')

h(doc, '4.5 LLM Parametreleri', 2)
tbl(doc, ['Parametre', 'Deger', 'Aciklama'],
    [['Model', 'llama3.2', 'Meta 3.2B parametre, acik kaynak'],
     ['Temperature', '0.3', 'Dusuk = deterministik, yaniltici degil'],
     ['num_ctx', '4096', 'Maksimum giris token penceresi'],
     ['keep_alive', '30m', 'Model sicak tutuluyor, yeniden yukleme yok'],
     ['Timeout', '8 sn', 'Asim halinde fallback devreye girer'],
     ['URL', 'localhost:11434', 'Yerel Ollama, internet gerektirmez']])

# ── 5. RAG DEGERLENDIRME ───────────────────────────────────────────────────────
h(doc, '5. RAG Sistem Degerlendirmesi — Gercek Test Sonuclari')
h(doc, '5.1 Degerlendirme Metodolojisi', 2)
p(doc, 'Script: logistics-backend/reports/evaluate_agent_rag.py')
p(doc, 'Test seti: data/rag_evaluation_dataset.csv — 7 sorgu, 4 metrik')
p(doc, 'Tum sorgular Ollama llama3.2 ile gercek zamanli test edildi. Generation Success: 7/7')

h(doc, '5.2 Metrik Tanimlari', 2)
tbl(doc, ['Metrik', 'Tanim', 'Formul', 'Bizim Skor'],
    [['Retriever Score', 'Alimlanan belgeler sorguyla iliskili mi?', 'keyword_overlap / required_keywords', '1.000'],
     ['Context Precision', 'Zorunlu anahtar kelimelerin kapsanmasi', 'hits / len(required_kw)', '0.786'],
     ['Hallucination Pass', 'LLM backend gercekleriyle uyumlu mu?', '1=gecti / 0=kaldi (celismi yok)', '1.000'],
     ['Answer Score', 'Cevap kalitesi (uzunluk + alakalilik)', 'grade_answer().score', '1.000']])

img(doc, '09_rag_average_scores.png', 6.3)
p(doc, 'Sekil 8: RAG Ortalama Metrik Skorlari. Retriever=1.0, Hallucination=1.0, Answer=1.0, Context Precision=0.786.')
img(doc, '10_rag_summary_statistics.png', 6.3)
p(doc, 'Sekil 9: RAG Ozet Istatistikler — 7 sorgu icin min/max/std dagilimi.')
img(doc, '11_rag_heatmap.png', 6.3)
p(doc, 'Sekil 10: RAG Isil Haritasi. Yesil=yuksek skor. Context Precision Q7 (trust) kategorisinde en dusuk — genis kavramli sorguda kelime eslesmesi daha zor.')
img(doc, '14_rag_per_question.png', 6.3)
p(doc, 'Sekil 11: Soru bazinda metrik dagilimi. Retriever, Hallucination ve Answer tum sorgularda 1.0. Context Precision Q3-Q6-Q7 arasi 0.50-0.67.')

h(doc, '5.3 Context Precision 0.786 — Neden Mukemmel Degil?', 2)
p(doc, 'Context Precision < 1.0 nedeni: TF-IDF kelime bazli esleme yapiyor. Q7 "trust" sorgusundaki "v9, r-squared, reliable, minimize cost" kelimelerinin hepsi tek belgede geçmiyor.')
p(doc, 'Buna ragmen: Hallucination Pass=1.0, Answer Score=1.0 — sistem DOGRU cevap uretiyor.')
p(doc, 'Iyilestirme yolu: Embedding tabanli retriever (BERT/sentence-transformers) Context Precision\'u arttirir. TF-IDF bizim sistemimiz icin yeterli performans saglamaktadir.')

# ── 6. OZET ─────────────────────────────────────────────────────────────────────
h(doc, '6. Sistem Dogrulugu — Genel Ozet')
tbl(doc, ['Bilesik', 'Metrik', 'Deger', 'Yorum'],
    [['ML Siniflandirici', 'AUC-ROC', '0.91', 'Gecikme tespiti cok guvenilir'],
     ['ML Siniflandirici', 'F2 Score', '0.82', 'Kacirilmis gecikme minimumda'],
     ['ML Regressor', 'R2 Score', '0.921', 'Varyansin %92si aciklaniyor'],
     ['ML Regressor', 'MAE', '5.36 dk', 'Ortalama mutlak hata — iyi'],
     ['P90 Regressor', 'Coverage', '%88.97', 'Gerceklerin %89u tampon altinda'],
     ['OR-Tools', 'Ort. Tasarruf', '%15.6', '5 senaryoda maliyet azaltimi'],
     ['RAG Retriever', 'Score', '1.000', 'Mukemmel — dogru belgeler'],
     ['RAG Halusinasyon', 'Pass Rate', '1.000', 'Hic yanlis bilgi gosterilmedi'],
     ['RAG Cevap', 'Score', '1.000', 'Kaliteli aciklama uretimi'],
     ['RAG Context', 'Precision', '0.786', 'Iyi — TF-IDF siniri, embedding ile artabilir']])

doc.save(os.path.join(OUT, 'sunum.docx'))
print("sunum.docx yazildi:", OUT)
