import urllib.parse
import streamlit as st
from sqlalchemy import create_engine

@st.cache_resource
def get_engine():
    try:
        # SecretsからURIを取得
        raw_uri = st.secrets["database"]["uri"]
        
        # もしパスワードに @ や : が含まれている場合の対策
        # postgres://user:pass@host... の形式を分解してエンコード
        if "@" in raw_uri:
            prefix, rest = raw_uri.split("://")
            user_pass, host_port_db = rest.rsplit("@", 1)
            if ":" in user_pass:
                user, password = user_pass.split(":", 1)
                # パスワードを安全な形式に変換
                safe_password = urllib.parse.quote_plus(password)
                raw_uri = f"{prefix}://{user}:{safe_password}@{host_port_db}"

        # SQLAlchemy用にプロトコルを補正
        if raw_uri.startswith("postgresql://"):
            raw_uri = raw_uri.replace("postgresql://", "postgresql+psycopg2://", 1)
            
        # SSL接続を強制
        if "?" not in raw_uri:
            raw_uri += "?sslmode=require"
        elif "sslmode" not in raw_uri:
            raw_uri += "&sslmode=require"
            
        return create_engine(raw_uri)
    except Exception as e:
        # 🚨 ここでエラーを隠さず画面に出す
        st.error(f"接続設定エラー: {str(e)}")
        return None

# init_dbもエラーを表示するように修正
def init_db():
    engine = get_engine()
    if engine is None: return
    
    try:
        with engine.connect() as conn:
            # (以下略: テーブル作成のSQL)
            pass
    except Exception as e:
        st.error(f"DB操作エラー: {str(e)}")
