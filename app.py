import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
import io
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font
import datetime

# --- AgGrid（Excelライクな表）のインポート ---
from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode, GridUpdateMode, JsCode

st.set_page_config(page_title="日本橋乙女 シフト管理", layout="wide")

st.markdown("""
<style>
div[data-testid="stButton"] button[kind="primary"] {
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
    color: white !important;
}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 1. データベース接続＆初期化
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
            drivername="postgresql+psycopg2",
            username=user,
            password=password,
            host=host,
            port=int(port),
            database=db_name,
            query={"sslmode": "require"},
        )
        return create_engine(url_object, poolclass=NullPool)
    except Exception as e:
        st.error(f"接続設定エラー: {e}")
        return None

engine = get_engine()

def init_db():
    if engine is None: return
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shift_data (
                day TEXT,
                staff_name TEXT,
                off_status TEXT,
                shift_json TEXT,
                PRIMARY KEY (day, staff_name)
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS staff_master (
                staff_name TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                role_name TEXT,
                is_admin BOOLEAN DEFAULT FALSE
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS system_config (
                config_key TEXT PRIMARY KEY,
                config_value TEXT
            );
        """))
        result = conn.execute(text("SELECT COUNT(*) FROM staff_master")).scalar()
        if result == 0:
            conn.execute(text("""
                INSERT INTO staff_master (staff_name, password, role_name, is_admin) 
                VALUES ('店長', 'admin1234', '全体統括', TRUE)
            """))
        conn.commit()

init_db()

def load_staff():
    return pd.read_sql("SELECT * FROM staff_master", engine)

def load_day_data(day_str):
    return pd.read_sql(f"SELECT * FROM shift_data WHERE day = '{day_str}'", engine)

def get_all_shift_data():
    return pd.read_sql("SELECT * FROM shift_data", engine)

def save_day_data(day_str, df):
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM shift_data WHERE day = '{day_str}'"))
        for _, row in df.iterrows():
            if row["氏名"] == "合計ライン": continue 
            shift_values = ",".join([str(row.get(t, "")) for t in time_slots])
            conn.execute(text("""
                INSERT INTO shift_data (day, staff_name, off_status, shift_json)
                VALUES (:day, :staff_name, :off_status, :shift_json)
            """), {"day": day_str, "staff_name": row["氏名"], "off_status": row["状態"], "shift_json": shift_values})
        conn.commit()

def save_config(key, value):
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO system_config (config_key, config_value) 
            VALUES (:key, :value) 
            ON CONFLICT (config_key) DO UPDATE SET config_value = :value
        """), {"key": key, "value": str(value)})
        conn.commit()

def get_config(key, default_value=""):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT config_value FROM system_config WHERE config_key = :key"), {"key": key}).scalar()
        return result if result else default_value

# ==========================================
# 2. セッション管理 & 固定値
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

time_slots = [f"{h}:{m:02d}" for h in range(10, 18) for m in (0, 30)]

if st.session_state.user is None:
    st.title("🔐 ログイン")
    with st.form("login_form"):
        input_name = st.text_input("氏名")
        input_pass = st.text_input("パスワード", type="password")
        submit = st.form_submit_button("ログイン", type="primary")
        if submit:
            staff_df = load_staff()
            user_row = staff_df[(staff_df['staff_name'] == input_name) & (staff_df['password'] == input_pass)]
            if not user_row.empty:
                st.session_state.user = {
                    "name": user_row.iloc[0]['staff_name'],
                    "is_admin": user_row.iloc[0]['is_admin'],
                    "role": user_row.iloc[0]['role_name']
                }
                st.rerun()
            else:
                st.error("氏名またはパスワードが間違っています。")
    st.stop()

st.sidebar.write(f"👤 **{st.session_state.user['name']}** さん")
if st.sidebar.button("ログアウト"):
    st.session_state.user = None
    st.rerun()

staff_df = load_staff()
staff_list = staff_df['staff_name'].tolist()

# ==========================================
# 3. 👨‍💼 管理者ダッシュボード
# ==========================================
if st.session_state.user["is_admin"]:
    st.title("👨‍💼 管理者ダッシュボード")
    tab1, tab2, tab3, tab4 = st.tabs(["📝 シフト編集", "📅 募集設定", "👥 スタッフ管理", "📊 Excel出力"])
    
    with tab1:
        st.write("### 📝 シフト編集（自動保存対応）")
        today = datetime.date.today()
        target_date = st.date_input("カレンダーから日付を選択", value=today)
        target_day_str = target_date.strftime("%Y-%m-%d")
        
        start_of_week = target_date - datetime.timedelta(days=target_date.weekday())
        end_of_week = start_of_week + datetime.timedelta(days=6)
        pd_start = pd.to_datetime(start_of_week)
        pd_end = pd.to_datetime(end_of_week)
        
        all_df = get_all_shift_data()
        all_df['date_obj'] = pd.to_datetime(all_df['day'], errors='coerce')
        all_df = all_df.dropna(subset=['date_obj'])
        week_df = all_df[(all_df['date_obj'] >= pd_start) & (all_df['date_obj'] <= pd_end)]
        
        weekly_hours = {}
        for s in staff_list:
            staff_week = week_df[week_df['staff_name'] == s]
            h_count = 0
            for _, r in staff_week.iterrows():
                slots = r["shift_json"].split(",")
                h_count += sum([1 for slot in slots if slot in ['1', '2', '同']])
            weekly_hours[s] = h_count * 0.5

        raw_df = load_day_data(target_day_str)
        display_data = []
        total_counts = {t: 0 for t in time_slots}
        
        for s in staff_list:
            match = raw_df[raw_df["staff_name"] == s]
            if not match.empty:
                status = match.iloc[0]["off_status"]
                if status not in ["OFF", "未提出"]: status = "提出済"
                row = {"氏名": s, "週勤務": f"{weekly_hours[s]:.1f}h", "状態": status}
                slots = match.iloc[0]["shift_json"].split(",")
                for j, t in enumerate(time_slots):
                    val = slots[j] if j < len(slots) else ""
                    row[t] = val
                    if val in ['1', '2', '同']: total_counts[t] += 1
            else:
                row = {"氏名": s, "週勤務": f"{weekly_hours[s]:.1f}h", "状態": "未提出", **{t: "" for t in time_slots}}
            display_data.append(row)
        
        total_row = {"氏名": "合計ライン", "週勤務": "-", "状態": "-"}
        for t in time_slots:
            total_row[t] = str(total_counts[t])
        display_data.append(total_row)
        
        df_to_edit = pd.DataFrame(display_data)

        # ==========================================
        # 🚨ここから：全体俯瞰 ＆ 【列移動ロック追加】設定
        # ==========================================
        editable_js = JsCode("function(params) { return params.node.data['氏名'] !== '合計ライン'; }")
        
        cell_style_js = JsCode("""
        function(params) {
            const v = params.value;
            let style = {'fontSize': '11px', 'textAlign': 'center', 'padding': '0px'};
            if (params.colDef.field === '氏名') {
                style['textAlign'] = 'left';
            }
            if (v === 'OFF' || v === '休' || v === '未提出') return Object.assign(style, {'backgroundColor': '#ffe6e6', 'color': '#cc0000', 'fontWeight': 'bold'});
            if (v === '1' || v === '2' || v === '同') return Object.assign(style, {'backgroundColor': '#e6f0ff', 'color': '#0044cc'});
            if (v === '提出済') return Object.assign(style, {'backgroundColor': '#e6ffe6', 'color': '#008000'});
            if (params.node.data['氏名'] === '合計ライン') return Object.assign(style, {'backgroundColor': '#f0f0f0', 'fontWeight': 'bold', 'borderTop': '2px solid #ccc'});
            return style;
        }
        """)

        work_calc_js = JsCode("""
        function(params) {
            if (params.node.data['氏名'] === '合計ライン') return '-';
            let active = 0;
            const ts = ['10:00','10:30','11:00','11:30','12:00','12:30','13:00','13:30','14:00','14:30','15:00','15:30','16:00','16:30','17:00','17:30'];
            ts.forEach(t => { if (['1', '2', '同'].includes(params.data[t])) active++; });
            return (active * 0.5).toFixed(1) + 'h';
        }
        """)
        
        break_calc_js = JsCode("""
        function(params) {
            if (params.node.data['氏名'] === '合計ライン') return '-';
            let brk = 0;
            const ts = ['10:00','10:30','11:00','11:30','12:00','12:30','13:00','13:30','14:00','14:30','15:00','15:30','16:00','16:30','17:00','17:30'];
            ts.forEach(t => { if (params.data[t] === '休') brk++; });
            return (brk * 0.5).toFixed(1) + 'h';
        }
        """)

        left_cols = [
            {"field": "氏名", "pinned": "left", "width": 85, "editable": False, "cellStyle": cell_style_js},
            {"field": "週勤務", "pinned": "left", "width": 55, "editable": False, "cellStyle": cell_style_js},
            {"field": "状態", "pinned": "left", "width": 65, "editable": editable_js, 
             "cellEditor": 'agSelectCellEditor', "cellEditorParams": {'values': ["未提出", "提出済", "OFF"]}, "cellStyle": cell_style_js}
        ]
        
        time_cols = []
        for h in range(10, 18):
            children = []
            for m in (0, 30):
                t = f"{h}:{m:02d}"
                children.append({
                    "field": t,
                    "headerName": f"{m:02d}", 
                    "width": 40, 
                    "editable": editable_js,
                    "cellEditor": 'agSelectCellEditor',
                    "cellEditorParams": {'values': ["", "1", "2", "同", "休"]},
                    "cellStyle": cell_style_js
                })
            time_cols.append({
                "headerName": f"{h}時",
                "children": children
            })
            
        right_cols = [
            {"field": "勤務h", "pinned": "right", "width": 55, "editable": False, "valueGetter": work_calc_js, "cellStyle": cell_style_js},
            {"field": "休憩h", "pinned": "right", "width": 55, "editable": False, "valueGetter": break_calc_js, "cellStyle": cell_style_js}
        ]

        # 🚨修正：表全体の設定に suppressMovableColumns と defaultColDef の suppressMovable を追加
        grid_options = {
            "columnDefs": left_cols + time_cols + right_cols,
            "defaultColDef": {
                "sortable": False, 
                "suppressMenu": True, 
                "resizable": True,
                "suppressMovable": True  # 列ごとの移動を禁止
            },
            "suppressMovableColumns": True, # 表全体の列ドラッグ移動を完全禁止
            "enableRangeSelection": True,
            "suppressCopyRowsToClipboard": True,
            "enterMovesDownAfterEdit": True,
            "singleClickEdit": True,
            "rowSelection": "multiple",
            "rowHeight": 24, 
            "headerHeight": 26, 
            "groupHeaderHeight": 26
        }
        
        custom_css = {
            ".ag-header-cell-text": {"font-size": "10px !important"},
            ".ag-header-group-text": {"font-size": "11px !important", "font-weight": "bold !important"}
        }
        
        st.info("💡 【操作ガイド】自動保存対応です！ セルの変更やコピペは、数秒以内に裏でデータベースに保存されます。")
        
        response = AgGrid(
            df_to_edit,
            gridOptions=grid_options,
            custom_css=custom_css,
            data_return_mode=DataReturnMode.AS_INPUT,
            update_mode=GridUpdateMode.VALUE_CHANGED,
            fit_columns_on_grid_load=False, 
            allow_unsafe_jscode=True, 
            theme='balham', 
            height=450
        )
        
        edited_df = pd.DataFrame(response['data'])
        if not edited_df.empty and not df_to_edit.empty:
            changed = False
            for s in staff_list:
                edited_row = edited_df[edited_df["氏名"] == s]
                orig_row = df_to_edit[df_to_edit["氏名"] == s]
                if not edited_row.empty and not orig_row.empty:
                    if str(edited_row.iloc[0]["状態"]) != str(orig_row.iloc[0]["状態"]):
                        changed = True
                        break
                    for t in time_slots:
                        if str(edited_row.iloc[0].get(t, "")) != str(orig_row.iloc[0].get(t, "")):
                            changed = True
                            break
                if changed: break
            
            if changed:
                save_day_data(target_day_str, edited_df)
                st.toast(f"✅ 自動保存しました！（{datetime.datetime.now().strftime('%H:%M:%S')}）", icon="💾")
                st.rerun() 

    # 【タブ2】募集設定
    with tab2:
        st.write("従業員画面に表示するシフト提出の対象期間を設定します。")
        col1, col2 = st.columns(2)
        with col1:
            period = st.date_input("📅 シフト対象期間", value=(today, today + datetime.timedelta(days=6)))
        with col2:
            deadline = st.date_input("⏳ 提出締め切り日", value=today + datetime.timedelta(days=3))
            
        if st.button("募集設定を更新", type="primary"):
            if isinstance(period, tuple) and len(period) == 2:
                save_config("period_start", period[0].strftime("%Y-%m-%d"))
                save_config("period_end", period[1].strftime("%Y-%m-%d"))
                save_config("deadline", deadline.strftime("%Y-%m-%d"))
                st.success("✅ 募集設定を更新しました！")

    # 【タブ3】スタッフ管理
    with tab3:
        st.write("#### 📁 CSVで一括インポート")
        @st.cache_data
        def get_csv_template():
            df_template = pd.DataFrame(columns=["氏名", "パスワード", "担当", "管理者権限"])
            df_template.loc[0] = ["テスト太郎", "pass123", "ホール", "FALSE"]
            return df_template.to_csv(index=False).encode('utf-8-sig')
            
        st.download_button("📥 インポート用CSVテンプレート", data=get_csv_template(), file_name="staff_template.csv", mime="text/csv")
        uploaded_file = st.file_uploader("CSVファイルをアップロード", type=["csv"])
        
        if uploaded_file:
            try:
                df_csv = pd.read_csv(uploaded_file)
                if list(df_csv.columns)[:4] != ["氏名", "パスワード", "担当", "管理者権限"]:
                    st.error("⚠️ 列の定義が異なります。")
                else:
                    if st.button("CSVデータをデータベースに保存", type="primary"):
                        with engine.connect() as conn:
                            for _, row in df_csv.iterrows():
                                is_admin_val = str(row["管理者権限"]).strip().lower() in ['true', '1', 'yes', 'はい']
                                conn.execute(text("""
                                    INSERT INTO staff_master (staff_name, password, role_name, is_admin)
                                    VALUES (:name, :pass, :role, :is_admin)
                                    ON CONFLICT (staff_name) DO UPDATE 
                                    SET password = :pass, role_name = :role, is_admin = :is_admin
                                """), {"name": str(row["氏名"]), "pass": str(row["パスワード"]), "role": str(row["担当"]), "is_admin": is_admin_val})
                            conn.commit()
                        st.success("✅ インポート完了！")
                        st.rerun()
            except Exception as e:
                st.error(f"エラー: {e}")
        
        st.write("---")
        st.write("#### ✍️ 手動編集")
        edited_staff = st.data_editor(staff_df, num_rows="dynamic", hide_index=True)
        if st.button("手動編集を保存"):
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM staff_master")) 
                for _, row in edited_staff[edited_staff['staff_name'].str.strip() != ""].iterrows():
                    conn.execute(text("""
                        INSERT INTO staff_master (staff_name, password, role_name, is_admin)
                        VALUES (:name, :pass, :role, :is_admin)
                    """), {"name": row["staff_name"], "pass": row["password"], "role": row["role_name"], "is_admin": row["is_admin"]})
                conn.commit()
            st.success("✅ 更新完了！")
            st.rerun()

    # 【タブ4】Excel出力
    with tab4:
        st.write("指定した期間のシフトをExcelとしてダウンロードします。")
        ex_start = st.date_input("開始日", value=today)
        ex_end = st.date_input("終了日", value=today + datetime.timedelta(days=6))
        
        def create_excel_file(start_date, end_date):
            output = io.BytesIO()
            wb = Workbook()
            wb.remove(wb.active)
            delta = end_date - start_date
            for i in range(delta.days + 1):
                d_date = start_date + datetime.timedelta(days=i)
                d_str = d_date.strftime("%Y-%m-%d")
                ws = wb.create_sheet(title=d_date.strftime("%m月%d日"))
                headers = ["氏名", "状態"] + time_slots
                ws.append(headers)
                df = load_day_data(d_str)
                for s in staff_list:
                    match = df[df["staff_name"] == s]
                    if not match.empty:
                        status = match.iloc[0]["off_status"]
                        if status not in ["OFF", "未提出"]: status = "提出済"
                        shift_vals = match.iloc[0]["shift_json"].split(",")
                        row_data = [s, status] + [shift_vals[i] if i < len(shift_vals) else "" for i in range(len(time_slots))]
                    else:
                        row_data = [s, "未提出"] + [""] * len(time_slots)
                    ws.append(row_data)
                for cell in ws[1]:
                    cell.font = Font(bold=True)
                    cell.fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")
                ws.column_dimensions['A'].width = 15
            wb.save(output)
            return output.getvalue()

        if ex_end >= ex_start:
            st.download_button(label="📥 指定期間のシフトを出力", data=create_excel_file(ex_start, ex_end), file_name=f"シフト表_{ex_start.strftime('%m%d')}-{ex_end.strftime('%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")

# ==========================================
# 4. 📱 従業員メニュー
# ==========================================
else:
    st.title("📱 シフト希望提出")
    p_start_str = get_config("period_start")
    p_end_str = get_config("period_end")
    p_deadline = get_config("deadline", "未設定")
    
    if p_start_str and p_end_str:
        st.warning(f"**現在の募集期間:** {p_start_str} 〜 {p_end_str} 　🚨 **提出締切:** {p_deadline}")
        p_start = datetime.datetime.strptime(p_start_str, "%Y-%m-%d").date()
        p_end = datetime.datetime.strptime(p_end_str, "%Y-%m-%d").date()
        target_dates = [(p_start + datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range((p_end - p_start).days + 1)]
        
        off_days = st.multiselect("お休み（OFF）希望の日付を選んでください", target_dates)
        
        if st.button("シフトを提出する", type="primary"):
            my_name = st.session_state.user["name"]
            for d_str in target_dates:
                df = load_day_data(d_str)
                status = "OFF" if d_str in off_days else "提出済"
                if my_name in df["staff_name"].values:
                    df.loc[df["staff_name"] == my_name, "off_status"] = status
                else:
                    new_row = pd.DataFrame([{"day": d_str, "staff_name": my_name, "off_status": status, "shift_json": ",".join([""]*len(time_slots))}])
                    df = pd.concat([df, new_row], ignore_index=True)
                
                save_df = pd.DataFrame([{"氏名": r["staff_name"], "状態": r["off_status"], **dict(zip(time_slots, r["shift_json"].split(",")))} for _, r in df.iterrows()])
                save_day_data(d_str, save_df)
                
            st.success("✅ あなたのシフト希望を提出しました！")
    else:
        st.error("現在、管理者によって設定されたシフト募集期間がありません。")
