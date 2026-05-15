# SBTU Lojistik Akıllı Dağıtım Sistemi - Proje Özeti

## 1. Projenin Amacı Nedir?
Bu proje, kurye rotalarını sadece mesafe bazlı değil, **makine öğrenmesi (ML) ile tahmin edilen gecikme risklerine** göre dinamik olarak optimize eden bir sistemdir. Kurye yola çıktıktan sonra değişen hava ve trafik koşullarına göre rotayı anlık olarak güncelleyebilir ve yapılan değişikliklerin nedenini Dispatcher'a (operatöre) **Yapay Zeka (LLM + RAG)** ile açıklar.

## 2. Sistem Mimarisi
- **Frontend**: React + Vite (Zustand state management, Mapbox GL JS)
- **Backend**: FastAPI (Python)
- **Veritabanı ve Önbellek**: PostgreSQL (Kalıcı veri) ve Redis (Pub/Sub & WebSocket simülasyonu)
- **Optimizasyon Motoru**: Google OR-Tools (VRP Çözücü)
- **Yapay Zeka**: Llama 3.2 (Ollama üzerinden lokal çalışır), CatBoost (ML modeli)

## 3. Yapay Zeka Nasıl ve Nerede Kullanılıyor?
Sistemde yapay zeka iki farklı katmanda çalışır ve görevleri kesin çizgilerle ayrılmıştır:

1. **Makine Öğrenmesi (CatBoost)**: Saf matematiksel tahmin yapar. Durak bazlı gecikme süresini (dakika cinsinden) hava, trafik ve yük durumuna göre tahmin eder. OR-Tools bu tahmini direkt *maliyet (cost)* fonksiyonuna ekleyerek rotayı çizer.
2. **Üretken Yapay Zeka (Llama 3.2 + RAG)**: Kesinlikle rota çizmez veya karar vermez. Sadece arka planda OR-Tools'un çizdiği rotanın *neden* çizildiğini operatöre açıklar. Bunu yaparken RAG (Retrieval-Augmented Generation) kullanarak geçmişteki benzer rota senaryolarından (örneğin karlı havalardaki teslimat başarıları) örnekler getirir.

## 4. Hallucination (Halüsinasyon) Engelleme Sistemi
Büyük Dil Modelleri (LLM) sayılar uydurmaya meyillidir. Operatöre "Rota 50 dakika kısaldı" deyip aslında 10 dakika kısalmış olması kritik bir lojistik hatasıdır.
Bunu önlemek için **Deterministic Grader (Doğrulayıcı)** yazılmıştır:
- LLM'in ürettiği metin taranır.
- İçindeki sayılar, backend'in ürettiği kesin matematiksel JSON sonuçlarıyla karşılaştırılır.
- Eğer LLM matematiksel olarak var olmayan bir sayı uydurursa (`hallucination_pass = False`), LLM'in cevabı tamamen çöpe atılır ve ekrana sadece deterministik arka plan mesajı basılır. Yüzde 100 güvenlik sağlanır.

## 5. Algoritmanın Matematiksel Mantığı
OR-Tools bir rotanın maliyetini hesaplarken sadece mesafeye bakmaz:
`Total Cost = (Mapbox Yol Süresi * Senaryo Çarpanı) + ML Gecikme Tahmini + Zaman Çizelgesi Cezası`

Örneğin son durağa geç kalındığında "Zaman Çizelgesi Cezası (Schedule Penalty)" üstel olarak artar. Bu sayede algoritma, gecikme riski yüksek olan durakları rotanın başına çekmeyi öğrenir.

## 6. Doğrulama ve Test
Sistem sadece makine öğrenmesi metrikleriyle (RMSE, MAE) değil, aynı zamanda yazılım mühendisliği prensipleriyle (21 PyTest backend testi) test edilmiştir. 
RAG sistemi Ragas framework'üne benzer bir yapıyla Context Precision, Faithfulness ve Hallucination Pass Rate metrikleriyle ölçülmüştür.

## Sonuç
Bu sistem, lojistik operasyonlarını otomatize ederken, insan denetimini kaybetmeden "Açıklanabilir Yapay Zeka (Explainable AI)" prensiplerini lojistik sektörüne entegre eden, üretime hazır (production-ready) bir prototiptir.
