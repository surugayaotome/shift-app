import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL  # 👈 これが足りませんでした！
import urllib.parse

st.set_page_config(page_title="シフト管理システム", layout="wide")

# --- データベース接続関数 ---
@st.cache_resource
def get_engine():
    try:
        if "database" not in st.secrets:
            st.error("Secretsに [database] が設定されていません。")
            return None
        
        raw_uri = st.secrets["database"]["uri"]
        
        # 1. URIをパーツに分解する（ドット入りのユーザー名を保護するため）
        # postgresql:// [user] : [pass] @ [host] : [port] / [db]
        prefix, rest = raw_uri.split("://")
        user_pass, host_db = rest.rsplit("@", 1)
        user, password = user_pass.split(":", 1)
        
        host_port, db_query = host_db.split("/", 1)
        host, port = host_port.split(":")
        
        db_name = db_query.split("?")[0]

        # 2. SQLAlchemyのURLオブジェクトを作成
        # これを使うと、特殊文字やドットを適切に処理してくれます
        url_object = URL.create(
            drivername="postgresql+psycopg2",
            username=user,         # postgres.pism... がここに入ります
            password=password,     # 英字のみ
            host=host,
            port=int(port),
            database=db_name,
            query={"sslmode": "require"},
        )
        
        return create_engine(url_object)
        
    except Exception as e:
        st.error(f"⚠️ 接続設定の分解に失敗しました: {e}")
        return None

# --- メイン処理 ---
st.title("🚀 シフト管理システム：接続テスト")

engine = get_engine()

if engine:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            st.success("✅ ついにデータベース接続に成功しました！")
            
            # テーブル作成
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
            st.info("テーブルの準備も完了です。アプリのメイン機能を実装できます。")
            
    except Exception as e:
        st.error("⚠️ データベースとの通信に失敗しました。")
        st.exception(e)  # 詳細なエラーを画面に出す
