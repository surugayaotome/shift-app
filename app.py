import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL  # インポート忘れ修正
import urllib.parse

st.set_page_config(page_title="日本橋乙女 シフト管理", layout="wide")

# --- データベース接続設定 ---
@st.cache_resource
def get_engine():
    try:
        # SecretsからURIを取得
        raw_uri = st.secrets["database"]["uri"]
        
        # 1. URIを自前で分解（ドット入りのユーザー名を保護）
        # postgresql:// [user] : [pass] @ [host] : [port] / [db]
        prefix, rest = raw_uri.split("://")
        user_pass, host_db = rest.rsplit("@", 1)
        user, password = user_pass.split(":", 1)
        host_port, db_query = host_db.split("/", 1)
        host, port = host_port.split(":")
        db_name = db_query.split("?")[0]

        # 2. パラメータを個別に指定してURLオブジェクトを作成（これが一番安全）
        url_object = URL.create(
            drivername="postgresql+psycopg2",
            username=user,
            password=password,
            host=host,
            port=int(port),
            database=db_name,
            query={"sslmode": "require"},
        )
        return create_engine(url_object)
    except Exception as e:
        st.error(f"接続設定の読み込みエラー: {e}")
        return None

# --- メイン処理 ---
st.title("🚀 データベース接続テスト")

engine = get_engine()

if engine:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            st.success("✅ ついにデータベースに接続できました！")
            
            # テーブル作成（QA工程として自動化）
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS shift_data (
                    day TEXT,
                    staff_name TEXT,
                    off_status TEXT,
                    shift_json TEXT,
                    PRIMARY KEY (day, staff_name)
                );
            """))
            conn.commit()
            st.info("テーブルの準備もOKです。ここからシフト管理のメイン機能を実装できます。")
            
    except Exception as e:
        st.error("⚠️ データベースとの通信に失敗しました。")
        st.exception(e) # 詳細なエラー（認証失敗など）を画面に出す
