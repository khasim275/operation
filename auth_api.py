from fastapi import APIRouter, Depends, Request, Response
from psycopg import Connection

from db import get_db
from security import LoginInput, UserInfo, current_user, login, logout

router = APIRouter(prefix="/api/auth")


@router.post("/login")
def sign_in(payload: LoginInput, response: Response, connection: Connection = Depends(get_db)) -> UserInfo:
    return login(payload, response, connection)


@router.post("/logout")
def sign_out(request: Request, response: Response, connection: Connection = Depends(get_db)):
    return logout(request, response, connection)


@router.get("/me")
def who_am_i(user: UserInfo = Depends(current_user)):
    return user
