import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
import io
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font
import datetime

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
        # 募集設定保存用のテーブルを追加
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

# DB操作系関数
def load_staff():
    return pd.read_sql("SELECT * FROM staff_master", engine)

def load_weekly_data(day):
    return pd.read_sql(f"SELECT * FROM shift_data WHERE day = '{day}'", engine)

def save_day_data(day, df):
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM shift_data WHERE day = '{day}'"))
        for _, row in df.iterrows():
            shift_values = ",".join([str(row.get(t, "")) for t in time_slots])
            conn.execute(text("""
                INSERT INTO shift_data (day, staff_name, off_status, shift_json)
                VALUES (:day, :staff_name, :off_status, :shift_json)
            """), {"day": day, "staff_name": row["氏名"], "off_status": row["休み"], "shift_json": shift_values})
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
# 2. セッション（ログイン）管理
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

days_of_week = ["月", "火", "水", "木", "金", "土", "日"]
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
st.sidebar.write(f"🏷️ 担当: {st.session_state.user['role']}")
if st.sidebar.button("ログアウト"):
    st.session_state.user = None
    st.rerun()
st.sidebar.markdown("---")

staff_df = load_staff()
staff_list = staff_df['staff_name'].tolist()

# ==========================================
# 3. メイン画面の分岐
# ==========================================
# 👨‍💼 管理者メニュー
if st.session_state.user["is_admin"]:
    st.title("👨‍💼 管理者ダッシュボード")
    tab1, tab2, tab3, tab4 = st.tabs(["📝 シフト編集", "📅 募集設定", "👥 スタッフ管理", "📊 Excel出力"])
    
    # 【タブ1】シフト編集
    with tab1:
        st.write("各曜日のシフトを調整・保存します。")
        day_tabs = st.tabs(days_of_week)
        for i, d in enumerate(days_of_week):
            with day_tabs[i]:
                raw_df = load_weekly_data(d)
                display_data = []
                for s in staff_list:
                    match = raw_df[raw_df["staff_name"] == s]
                    if not match.empty:
                        row = {"氏名": s, "休み": match.iloc[0]["off_status"]}
                        slots = match.iloc[0]["shift_json"].split(",")
                        for j, t in enumerate(time_slots):
                            row[t] = slots[j] if j < len(slots) else ""
                    else:
                        row = {"氏名": s, "休み": "", **{t: "" for t in time_slots}}
                    display_data.append(row)
                
                df_to_edit = pd.DataFrame(display_data)
                col_config = {"氏名": st.column_config.TextColumn(disabled=True), "休み": st.column_config.TextColumn(disabled=True)}
                for t in time_slots: 
                    col_config[t] = st.column_config.SelectboxColumn(t, options=["", "1", "2", "同", "休"], width="small")
                
                edited_df = st.data_editor(df_to_edit, column_config=col_config, hide_index=True, key=f"editor_{d}")
                if st.button(f"💾 {d}曜日の変更を保存", key=f"save_{d}"):
                    save_day_data(d, edited_df)
                    st.toast(f"✅ {d}曜日のデータを更新しました！")

    # 【タブ2】募集設定（カレンダー）
    with tab2:
        st.write("従業員画面に表示するシフト提出の対象期間と、締め切り日を設定します。")
        
        # デフォルトで今日から1週間分を計算
        today = datetime.date.today()
        default_period = (today, today + datetime.timedelta(days=6))
        
        col1, col2 = st.columns(2)
        with col1:
            period = st.date_input("📅 シフト対象期間 (開始日 - 終了日)", value=default_period)
        with col2:
            deadline = st.date_input("⏳ 提出締め切り日", value=today + datetime.timedelta(days=3))
            
        if st.button("募集設定を更新", type="primary"):
            if isinstance(period, tuple) and len(period) == 2:
                save_config("period_start", period[0].strftime("%Y-%m-%d"))
                save_config("period_end", period[1].strftime("%Y-%m-%d"))
                save_config("deadline", deadline.strftime("%Y-%m-%d"))
                st.success("✅ 募集設定を更新しました！従業員画面に反映されます。")
            else:
                st.error("⚠️ 期間の「開始日」と「終了日」の両方を選択してください。")

    # 【タブ3】スタッフ管理（手動入力 ＋ CSVアップロード）
    with tab2: # Wait, tabs changed. Correcting to tab3
        pass # Overwritten below properly to avoid indent issues.

    with tab3:
        st.write("従業員の手動追加・パスワード設定、またはCSVでの一括登録を行います。")
        
        st.write("#### 📁 CSVで一括インポート")
        st.info("A列から順に「氏名」「パスワード」「担当」「管理者権限(TRUE/FALSE)」となるCSVファイルを作成してください。")
        uploaded_file = st.file_uploader("CSVファイルをアップロード", type=["csv"])
        
        if uploaded_file:
            try:
                df_csv = pd.read_csv(uploaded_file)
                expected_cols = ["氏名", "パスワード", "担当", "管理者権限"]
                
                # A, B, C, Dの列が正確にマッピングされているか検証
                if list(df_csv.columns)[:4] != expected_cols:
                    st.error(f"⚠️ 列の定義が異なります。1行目は左から {expected_cols} の順にしてください。")
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
                                """), {
                                    "name": str(row["氏名"]),
                                    "pass": str(row["パスワード"]),
                                    "role": str(row["担当"]),
                                    "is_admin": is_admin_val
                                })
                            conn.commit()
                        st.success("✅ CSVからのインポートが完了しました！")
                        st.rerun()
            except Exception as e:
                st.error(f"CSV読み込みエラー: {e}")
        
        st.write("---")
        st.write("#### ✍️ 手動編集")
        edited_staff = st.data_editor(
            staff_df,
            column_config={
                "staff_name": st.column_config.TextColumn("氏名 (必須)"),
                "password": st.column_config.TextColumn("パスワード"),
                "role_name": st.column_config.TextColumn("担当/役割"),
                "is_admin": st.column_config.CheckboxColumn("管理者権限")
            },
            num_rows="dynamic",
            hide_index=True,
            key="staff_editor"
        )
        if st.button("手動編集を保存"):
            valid_staff = edited_staff[edited_staff['staff_name'].str.strip() != ""]
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM staff_master")) 
                for _, row in valid_staff.iterrows():
                    conn.execute(text("""
                        INSERT INTO staff_master (staff_name, password, role_name, is_admin)
                        VALUES (:name, :pass, :role, :is_admin)
                    """), {
                        "name": row["staff_name"], 
                        "pass": row["password"], 
                        "role": row["role_name"], 
                        "is_admin": row["is_admin"]
                    })
                conn.commit()
            st.success("✅ スタッフ情報を更新しました！")
            st.rerun()

    # 【タブ4】Excel出力
    with tab4:
        st.write("現在のデータベースの状態をExcelとしてダウンロードします。")
        def create_excel_file():
            output = io.BytesIO()
            wb = Workbook()
            wb.remove(wb.active)
            for d in days_of_week:
                ws = wb.create_sheet(title=f"{d}曜日")
                headers = ["氏名", "休み"] + time_slots
                ws.append(headers)
                df = load_weekly_data(d)
                for s in staff_list:
                    match = df[df["staff_name"] == s]
                    if not match.empty:
                        off_val = match.iloc[0]["off_status"]
                        shift_vals = match.iloc[0]["shift_json"].split(",")
                        row_data = [s, off_val] + [shift_vals[i] if i < len(shift_vals) else "" for i in range(len(time_slots))]
                    else:
                        row_data = [s, ""] + [""] * len(time_slots)
                    ws.append(row_data)
                for cell in ws[1]:
                    cell.font = Font(bold=True)
                    cell.fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")
                ws.column_dimensions['A'].width = 15
            wb.save(output)
            return output.getvalue()

        st.download_button(
            label="📥 全曜日のシフトをExcelでダウンロード",
            data=create_excel_file(),
            file_name="シフト表_最新版.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )

# 📱 従業員メニュー（一般権限）
else:
    st.title("📱 シフト希望提出")
    st.info(f"ログイン中のユーザー: **{st.session_state.user['name']}** さん")
    
    # DBから募集設定を読み込んで表示
    p_start = get_config("period_start", "未設定")
    p_end = get_config("period_end", "未設定")
    p_deadline = get_config("deadline", "未設定")
    
    st.warning(f"**現在の募集期間:** {p_start} 〜 {p_end} 　🚨 **提出締切:** {p_deadline}")
    
    off_days = st.multiselect("お休み（OFF）希望の曜日を選んでください", days_of_week)
    
    if st.button("シフトを提出する", type="primary"):
        my_name = st.session_state.user["name"]
        for d in days_of_week:
            df = load_weekly_data(d)
            off_status = "OFF" if d in off_days else ""
            
            if my_name in df["staff_name"].values:
                df.loc[df["staff_name"] == my_name, "off_status"] = off_status
            else:
                new_row = pd.DataFrame([{"day": d, "staff_name": my_name, "off_status": off_status, "shift_json": ",".join([""]*len(time_slots))}])
                df = pd.concat([df, new_row], ignore_index=True)
            
            save_df = pd.DataFrame([{"氏名": r["staff_name"], "休み": r["off_status"], **dict(zip(time_slots, r["shift_json"].split(",")))} for _, r in df.iterrows()])
            save_day_data(d, save_df)
            
        st.success("✅ あなたのシフト希望を提出しました！")
