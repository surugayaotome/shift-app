import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
import io
import datetime
import openpyxl
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# --- AgGridのインポート ---
from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode, GridUpdateMode, JsCode

st.set_page_config(page_title="日本橋乙女 シフト管理", layout="wide")

# 🚨 1. CSSで見切れ・警告・デザインを物理的に解決
st.markdown("""
<style>
/* セル内の余白をゼロにし、文字が絶対に見切れないように強制 */
.ag-theme-balham .ag-cell {
    padding-left: 0px !important;
    padding-right: 0px !important;
    text-overflow: clip !important;
    font-size: 10px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}
/* ヘッダー文字の見切れ対策 */
.ag-header-cell-label {
    justify-content: center !important;
    padding: 0 !important;
}
.ag-header-cell-text {
    font-size: 10px !important;
    text-overflow: clip !important;
}
/* Streamlitのボタン色 */
div[data-testid="stButton"] button[kind="primary"] {
    background-color: #ff4b4b !important;
    color: white !important;
}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. データベース接続
# ==========================================
@st.cache_resource
def get_engine():
    try:
        raw_uri = st.secrets["database"]["uri"]
        prefix, rest = raw_uri.split("://")
        user_pass, host_db = rest.rsplit("@", 1)
        user, password = user_pass.split(":", 1)
        host_port, db_query = host_db.split("/", 1)
        host, port = host_port.split(":")
        db_name = db_query.split("?")[0]
        url_object = URL.create(
            drivername="postgresql+psycopg2", username=user, password=password,
            host=host, port=int(port), database=db_name, query={"sslmode": "require"},
        )
        return create_engine(url_object, poolclass=NullPool)
    except Exception as e:
        st.error(f"DB接続エラー: {e}")
        return None

engine = get_engine()

def init_db():
    if engine is None: return
    # 各テーブル作成（1つずつ確定させる）
    for cmd in [
        "CREATE TABLE IF NOT EXISTS shift_data (day TEXT, staff_name TEXT, off_status TEXT, shift_json TEXT, PRIMARY KEY (day, staff_name));",
        "CREATE TABLE IF NOT EXISTS staff_master (staff_name TEXT PRIMARY KEY, password TEXT NOT NULL, role_name TEXT, is_admin BOOLEAN DEFAULT FALSE, staff_id TEXT);",
        "CREATE TABLE IF NOT EXISTS system_config (config_key TEXT PRIMARY KEY, config_value TEXT);"
    ]:
        try:
            with engine.begin() as conn:
                conn.execute(text(cmd))
        except: pass

    # staff_idカラムがない場合のためのALTER（エラー道連れ防止）
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE staff_master ADD COLUMN staff_id TEXT;"))
    except: pass

init_db()

# ==========================================
# 3. データ処理・関数
# ==========================================
def load_day_data(day_str):
    return pd.read_sql(text("SELECT * FROM shift_data WHERE day = :d"), engine, params={"d": day_str})

def get_all_shift_data():
    return pd.read_sql(text("SELECT * FROM shift_data"), engine)

def save_day_data(day_str, df):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM shift_data WHERE day = :d"), {"d": day_str})
        for _, row in df.iterrows():
            if row["氏名"] == "合計ライン": continue 
            shift_vals = ",".join([str(row.get(t, "")) for t in time_slots])
            conn.execute(text("""
                INSERT INTO shift_data (day, staff_name, off_status, shift_json)
                VALUES (:day, :name, :off, :json)
            """), {"day": day_str, "name": row["氏名"], "off": row["本人希望"], "json": shift_vals})

def save_config(key, value):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO system_config (config_key, config_value) VALUES (:k, :v) ON CONFLICT (config_key) DO UPDATE SET config_value = :v"), {"k": key, "v": str(value)})

def get_config(key, default=""):
    try:
        with engine.connect() as conn:
            val = conn.execute(text("SELECT config_value FROM system_config WHERE config_key = :k"), {"k": key}).scalar()
            return val if val else default
    except: return default

# ==========================================
# 4. メイン・アプリ
# ==========================================
time_slots = [f"{h}:{m:02d}" for h in range(8, 23) for m in (0, 30)]

if "user" not in st.session_state:
    st.session_state.user = None

if st.session_state.user is None:
    st.title("🔐 日本橋乙女 シフト管理ログイン")
    with st.form("login_form"):
        n = st.text_input("氏名")
        p = st.text_input("パスワード", type="password")
        if st.form_submit_button("ログイン", type="primary"):
            sdf = pd.read_sql(text("SELECT * FROM staff_master WHERE staff_name = :n AND password = :p"), engine, params={"n":n, "p":p})
            if not sdf.empty:
                st.session_state.user = {"name": sdf.iloc[0]['staff_name'], "is_admin": sdf.iloc[0]['is_admin']}
                st.rerun()
            else: st.error("氏名またはパスワードが違います。")
    st.stop()

st.sidebar.write(f"👤 ログイン中: {st.session_state.user['name']}")
if st.sidebar.button("ログアウト"):
    st.session_state.user = None
    st.rerun()

# スタッフマスター取得
staff_df_master = pd.read_sql(text("SELECT * FROM staff_master"), engine)
staff_list = staff_df_master['staff_name'].tolist()
staff_id_map = dict(zip(staff_df_master['staff_name'], staff_df_master['staff_id']))

# 👨‍💼 管理者メニュー
if st.session_state.user["is_admin"]:
    st.title("👨‍💼 管理者メニュー")
    tab1, tab2, tab3, tab4 = st.tabs(["📝 シフト編集", "📅 募集設定", "👥 スタッフ管理", "📊 Excel出力"])

    # --- タブ1: シフト編集（画像1再現） ---
    with tab1:
        target_date = st.date_input("表示日を選択", value=datetime.date.today())
        target_day_str = target_date.strftime("%Y-%m-%d")
        
        # 週勤務時間計算（🚨 UserWarning対策：format指定）
        start_of_week = target_date - datetime.timedelta(days=target_date.weekday())
        pd_start = pd.to_datetime(start_of_week)
        pd_end = pd_start + datetime.timedelta(days=6)
        
        all_df = get_all_shift_data()
        all_df['date_obj'] = pd.to_datetime(all_df['day'], format="%Y-%m-%d", errors='coerce')
        all_df = all_df.dropna(subset=['date_obj'])
        week_df = all_df[(all_df['date_obj'] >= pd_start) & (all_df['date_obj'] <= pd_end)]
        
        display_data = []
        total_counts = {t: 0 for t in time_slots}
        raw_day = load_day_data(target_day_str)
        
        for s in staff_list:
            match = raw_day[raw_day["staff_name"] == s]
            wk_h = 0
            if not week_df.empty:
                s_wk = week_df[week_df['staff_name'] == s]
                wk_h = s_wk['shift_json'].str.split(',').explode().isin(['1','2','同']).sum() * 0.5
            
            if not match.empty:
                row = {"ID": staff_id_map.get(s, ""), "氏名": s, "週勤務時間": f"{wk_h:.1f}", "本人希望": match.iloc[0]["off_status"]}
                slots = match.iloc[0]["shift_json"].split(",")
                for j, t in enumerate(time_slots):
                    val = slots[j] if j < len(slots) else ""
                    row[t] = val
                    if val in ['1', '2', '同']: total_counts[t] += 1
            else:
                row = {"ID": staff_id_map.get(s, ""), "氏名": s, "週勤務時間": f"{wk_h:.1f}", "本人希望": "", **{t: "" for t in time_slots}}
            display_data.append(row)
        
        display_data.append({"ID": "", "氏名": "合計ライン", "週勤務時間": "-", "本人希望": "", **{t: str(total_counts[t]) for t in time_slots}})
        df_to_edit = pd.DataFrame(display_data)

        # 🚨 最強のAgGrid JS設定（見切れ・比較機能込み）
        cell_style_js = JsCode("""
        function(params) {
            const v = params.value;
            const hope = params.node.data['本人希望'] || "";
            const field = params.colDef.field;
            let style = {'fontSize': '10px', 'textAlign': 'center', 'padding': '0px', 'borderRight': '1px solid #e2e2e2', 'textOverflow': 'clip'};

            // 💡 比較機能: 希望時間外（例: 10-15以外）にシフトがある場合にオレンジ色
            if (['1','2','同'].includes(v) && hope.includes('-')) {
                const parts = hope.split('-');
                if (parts.length === 2) {
                    const hStart = Number(parts[0]); const hEnd = Number(parts[1]);
                    const currentH = Number(field.split(':')[0]);
                    if (currentH < hStart || currentH >= hEnd) style['backgroundColor'] = '#ffcc00';
                }
            }
            if (v === 'OFF' || v === '休') return Object.assign(style, {'backgroundColor': '#ff0000', 'color': '#ffffff'});
            if (v === '1') return Object.assign(style, {'backgroundColor': '#fce4d6'});
            if (v === '2') return Object.assign(style, {'backgroundColor': '#ffff00'});
            if (v === '同') return Object.assign(style, {'backgroundColor': '#00b050', 'color': '#ffffff'});
            if (params.node.data['氏名'] === '合計ライン') return Object.assign(style, {'backgroundColor': '#ffff00', 'fontWeight': 'bold'});
            return style;
        }
        """)

        work_calc_js = JsCode("function(params) { if (params.node.data['氏名'] === '合計ライン') return '-'; let a = 0; const ts = [" + ",".join([f"'{t}'" for t in time_slots]) + "]; ts.forEach(t => { if (['1','2','同'].includes(params.data[t])) a++; }); return (a*0.5).toFixed(1); }")
        break_calc_js = JsCode("function(params) { if (params.node.data['氏名'] === '合計ライン') return '-'; let b = 0; const ts = [" + ",".join([f"'{t}'" for t in time_slots]) + "]; ts.forEach(t => { if (params.data[t] === '休') b++; }); return (b*0.5).toFixed(1); }")

        # 列構築
        left_cols = [
            {"field": "ID", "pinned": "left", "width": 45, "cellStyle": cell_style_js},
            {"field": "氏名", "pinned": "left", "width": 85, "cellStyle": cell_style_js},
            {"headerName": "勤務h", "field": "勤務h", "pinned": "left", "width": 45, "valueGetter": work_calc_js, "cellStyle": cell_style_js},
            {"headerName": "休憩h", "field": "休憩h", "pinned": "left", "width": 45, "valueGetter": break_calc_js, "cellStyle": cell_style_js},
            {"headerName": "希望", "field": "本人希望", "pinned": "left", "width": 65, "editable": True, "cellStyle": cell_style_js}
        ]
        mid_cols = [{"headerName": f"{h}", "children": [{"field": f"{h}:{m:02d}", "headerName": "", "width": 25, "editable": True, "cellEditor": 'agSelectCellEditor', "cellEditorParams": {'values': ["", "1", "2", "同", "休"]}, "cellStyle": cell_style_js} for m in (0, 30)]} for h in range(8, 23)]
        right_cols = [{"field": "週勤務時間", "pinned": "right", "width": 60, "cellStyle": cell_style_js}]

        grid_opts = {
            "columnDefs": left_cols + mid_cols + right_cols,
            "defaultColDef": {"sortable": False, "suppressMenu": True, "resizable": True, "suppressMovable": True},
            "enableRangeSelection": True, "suppressCopyRowsToClipboard": True, "enterMovesDownAfterEdit": True, "singleClickEdit": True, "rowHeight": 22, "headerHeight": 22, "groupHeaderHeight": 22
        }
        
        st.info("💡 変更は即座に自動保存されます。希望と違うシフトはオレンジ色で警告されます。")
        res = AgGrid(df_to_edit, gridOptions=grid_opts, update_mode=GridUpdateMode.VALUE_CHANGED, fit_columns_on_grid_load=False, allow_unsafe_jscode=True, theme='balham', height=500)
        
        # 🚨 オートセーブ
        if not pd.DataFrame(res['data']).equals(df_to_edit):
            save_day_data(target_day_str, pd.DataFrame(res['data']))
            st.toast("✅ 自動保存完了")
            st.rerun()

    # --- タブ2: 募集設定 ---
    with tab2:
        st.write("### 📅 シフト募集・締め切り設定")
        col1, col2 = st.columns(2)
        with col1:
            p = st.date_input("募集期間", value=(datetime.date.today(), datetime.date.today()+datetime.timedelta(days=6)))
        with col2:
            d = st.date_input("提出期限", value=datetime.date.today()+datetime.timedelta(days=3))
        if st.button("設定を保存", type="primary"):
            if isinstance(p, tuple) and len(p) == 2:
                save_config("p_start", p[0].strftime("%Y-%m-%d"))
                save_config("p_end", p[1].strftime("%Y-%m-%d"))
                save_config("p_deadline", d.strftime("%Y-%m-%d"))
                st.success("設定を更新しました。")

    # --- タブ3: スタッフ管理 ---
    with tab3:
        st.write("### 👥 スタッフマスター管理")
        edited_staff = st.data_editor(staff_df_master, num_rows="dynamic", hide_index=True)
        if st.button("DBを更新する"):
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM staff_master"))
                for _, r in edited_staff.iterrows():
                    if pd.notna(r['staff_name']):
                        conn.execute(text("INSERT INTO staff_master (staff_id, staff_name, password, role_name, is_admin) VALUES (:id, :n, :p, :r, :a)"), {"id": r['staff_id'], "n": r['staff_name'], "p": r['password'], "r": r['role_name'], "a": r['is_admin']})
            st.success("スタッフ情報を保存しました。")
            st.rerun()

    # --- タブ4: Excel出力（🚨Image 2 週間タイル完全再現） ---
    with tab4:
        st.write("### 📊 Excelダウンロード（Image 2再現版）")
        mode = st.radio("出力形式", ["日別出力", "週間タイル出力（Image 2）"])
        ex_date = st.date_input("開始日", value=datetime.date.today())

        def write_excel_day(ws, r_start, c_start, target_d):
            d_str = target_d.strftime("%Y-%m-%d")
            ws.cell(r_start, c_start, f"{target_d.strftime('%m/%d')} ({target_d.strftime('%a')})").font = Font(bold=True)
            
            headers = ["ID", "氏名", "勤務h", "休憩h", "休み"] + time_slots + ["合計"]
            for i, h in enumerate(headers):
                cell = ws.cell(r_start+1, c_start+i, h if ":" not in h else "")
                cell.font = Font(size=8, bold=True); cell.fill = PatternFill(start_color="DDDDDD", fill_type="solid")
                cell.border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

            day_data = load_day_data(d_str)
            curr = r_start + 2
            total_slots = [0] * len(time_slots)

            for s in staff_list:
                m = day_data[day_data["staff_name"] == s]
                s_id = staff_id_map.get(s, "")
                if not m.empty:
                    slots = m.iloc[0]["shift_json"].split(",")
                    wh = sum([1 for x in slots if x in ['1','2','同']]) * 0.5
                    bh = sum([1 for x in slots if x == '休']) * 0.5
                    vals = [s_id, s, wh, bh, m.iloc[0]["off_status"]] + slots + [wh]
                else:
                    vals = [s_id, s, 0, 0, ""] + [""] * len(time_slots) + [0]
                
                for i, v in enumerate(vals):
                    cell = ws.cell(curr, c_start+i, v)
                    cell.font = Font(size=8); cell.alignment = Alignment(horizontal='center')
                    # 色付け
                    if v == '1': cell.fill = PatternFill(start_color="FCE4D6", fill_type="solid")
                    elif v == '2': cell.fill = PatternFill(start_color="FFFF00", fill_type="solid")
                    elif v == '同': cell.fill = PatternFill(start_color="00B050", fill_type="solid"); cell.font = Font(size=8, color="FFFFFF")
                    elif v in ['休', 'OFF']: cell.fill = PatternFill(start_color="FF0000", fill_type="solid"); cell.font = Font(size=8, color="FFFFFF")
                    
                    if 5 <= i < 5 + len(time_slots) and v in ['1','2','同']:
                        total_slots[i-5] += 1
                curr += 1
            
            # 合計ライン
            ws.cell(curr, c_start+1, "合計ライン").fill = PatternFill(start_color="FFFF00", fill_type="solid")
            for i, val in enumerate(total_slots):
                ws.cell(curr, c_start+5+i, val).fill = PatternFill(start_color="FFFF00", fill_type="solid")
            return curr + 2

        if st.button("Excelを作成"):
            output = io.BytesIO(); wb = Workbook(); ws = wb.active; ws.title = "Shift"
            # 列幅を極限まで狭く
            for i in range(1, 150): ws.column_dimensions[get_column_letter(i)].width = 2.5
            ws.column_dimensions['B'].width = 10 # 氏名だけ少し広く
            
            if mode == "日別出力":
                write_excel_day(ws, 2, 1, ex_date)
            else:
                # 🚨 Image 2再現: 左(月火水木)、右(金土日)
                offset = len(time_slots) + 7
                l_r, r_r = 2, 2
                for i in range(7):
                    d = ex_date + datetime.timedelta(days=i)
                    if i < 4: l_r = write_excel_day(ws, l_r, 1, d)
                    else: r_r = write_excel_day(ws, r_r, offset, d)
            
            wb.save(output)
            st.download_button("📥 Excelを保存", output.getvalue(), f"Shift_{ex_date.strftime('%m%d')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# 📱 従業員画面
else:
    st.title("📱 シフト希望提出")
    ps, pe = get_config("p_start"), get_config("p_end")
    if ps and pe:
        st.info(f"募集期間: {ps} 〜 {pe}")
        for dstr in pd.date_range(ps, pe).strftime("%Y-%m-%d"):
            with st.expander(f"📅 {dstr} の希望"):
                hope = st.text_input("希望 (例: OFF, 10-15)", key=f"h_{dstr}")
                if st.button(f"保存", key=f"b_{dstr}"):
                    with engine.begin() as conn:
                        cur = conn.execute(text("SELECT shift_json FROM shift_data WHERE day=:d AND staff_name=:n"), {"d":dstr, "n":st.session_state.user["name"]}).scalar()
                        j = cur if cur else ",".join([""]*len(time_slots))
                        conn.execute(text("INSERT INTO shift_data (day, staff_name, off_status, shift_json) VALUES (:d, :n, :o, :j) ON CONFLICT (day, staff_name) DO UPDATE SET off_status=:o"), {"d":dstr, "n":st.session_state.user["name"], "o":hope, "j":j})
                    st.toast("保存完了")
    else: st.write("現在募集していません。")
