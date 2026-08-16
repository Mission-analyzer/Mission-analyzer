# Збірка .exe та публікація релізу

Коротка інструкція для себе ж — щоб не згадувати наново кожен раз.

## 1. Одноразово (на новій машині / новому Python)

```
python -m pip install -r requirements.txt
python -m pip install pyinstaller
```

Перевірити, що все стало саме в той Python, з якого будеш збирати:

```
where python
python -m pip list
```

У списку мають бути `pymavlink`, `pyserial`, `Pillow`, `reportlab`, `pyinstaller`.

## 2. Перед кожним релізом

1. Оновити `VERSION` у `meta.py` (наприклад `"1.0.0"` → `"1.1.0"`) і додати новий запис на початок `ENTRIES` — це і changelog на сторінці «Довідка», і ім'я майбутнього `.exe`/папки, звідси й береться.
2. Переконатись, що `icon.png` (і, бажано, `icon.ico` — саме для іконки самого `.exe`) лежать поруч з `main.py`.

## 3. Сама збірка

```
cd /d "E:\Mission analyzer"
python -m PyInstaller main.spec --noconfirm
```

Результат: `dist\MissionAnalyzerV<версія>\` — усередині `MissionAnalyzerV<версія>.exe` і папка `_internal` поруч. Ім'я підставляється саме з `meta.VERSION`, вручну в `main.spec` нічого міняти не треба.

**Перевірка на чистому запуску:**
- відкривається без чорного консольного вікна;
- є іконка/лого;
- «Місія» → «Завантажити» → карта малюється;
- «Аналіз» → «Зберегти PDF» → кирилиця в PDF не кракозябри;
- якщо є ArduPilot — Read/Write по MAVLink реально працює (це якраз те місце, де без потрібних hiddenimports у `main.spec` збірка мовчки проходить, а падає тільки в момент реального підключення).

## 4. Публікація на GitHub

1. Заархівувати папку `dist\MissionAnalyzerV<версія>` **цілком** (right-click → «Відправити» → «Стиснута (zip) папка»). Назва архіву — наприклад `MissionAnalyzerV100.zip`.
2. На сторінці репозиторію → вкладка **Releases** → **Create a new release**.
3. **Tag**: `v<версія>` (наприклад `v1.0.0`), на поточному коміті `main`.
4. Перетягнути zip у **Attach binaries**.
5. В опис — скопіювати відповідний запис з `meta.ENTRIES` (той самий текст, що й на сторінці «Довідка»), не писати заново.
6. **Publish release**.

Сам `.exe`/`dist/`/`build/` у git НЕ комітяться (вже в `.gitignore`) — тільки код і `main.spec`, білд — окремим файлом у релізі.

## 5. Типові проблеми при збірці

**`Fatal error in launcher: Unable to create process using ... pyinstaller.exe ...`**
Лаунчер `pyinstaller.exe` запам'ятав шлях до Python, з якого його ставили, а той Python могли перевстановити/оновити. Обхід — викликати як модуль поточного Python, а не окремий `.exe`:
```
python -m PyInstaller main.spec --noconfirm
```

**`No module named PyInstaller`**
На машині кілька Python (наприклад різні користувачі/версії), і `pyinstaller` стоїть не під тим, який зараз резолвиться як `python`. Перевстановити під поточний:
```
python -m pip install -r requirements.txt
python -m pip install pyinstaller
```

**Обвал/`RecursionError` десь у `hook-matplotlib.py`**
Якщо в системному Python випадково стоїть `matplotlib` (програма його не використовує — графіки малюються напряму на `tkinter.Canvas`), PyInstaller може спробувати проаналізувати його граф залежностей і впасти. В `main.spec` це вже явно виключено (`excludes=['matplotlib', 'numpy', 'scipy', 'pandas']`) — якщо ловиш подібне знову після якихось змін у spec-файлі, перевір, що цей рядок не загубився.

**Windows SmartScreen / антивірус лається на готовий `.exe`**
Нормально для непідписаного білда без сертифіката — «Докладніше» → «Виконати попри все». Якщо саме Defender **видаляє** файл (не просто попереджає) — спробувати `upx=False` в обох місцях `main.spec` (`EXE` і `COLLECT`): UPX-стиснуті бінарники частіше дають false positive.
