"""Korail 7.0.8 compatibility client and DynaPath v1.0.3 implementation.

Fixes Korail login and search failures caused by WAF blacklisting the legacy
korail2 device ID and outdated protocol parameters.
"""

import base64
import hashlib
import hmac
import json
import random
import re
import string
import struct
import time
from secrets import token_bytes
from typing import Optional, List, Tuple
from functools import reduce
from datetime import datetime, timedelta

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from korail2 import (
    Korail as BaseKorail,
    TrainType,
    ReserveOption,
    Passenger,
    AdultPassenger,
    ChildPassenger,
    ToddlerPassenger,
    SeniorPassenger,
    SoldOutError,
    NoResultsError,
    NeedToLoginError,
    KorailError,
)
from korail2.korail2 import Train, Reservation
from utils.logger import get_logger

logger = get_logger(__name__)

# Constants for Korail 7.0.8
API_HOST = "smart.letskorail.com"
KORAIL_MOBILE = f"https://{API_HOST}:443/classes/com.korail.mobile"
KORAIL_LOGIN = f"{KORAIL_MOBILE}.login.Login"
KORAIL_LOGOUT = f"{KORAIL_MOBILE}.common.logout"
KORAIL_SEARCH_SCHEDULE = f"{KORAIL_MOBILE}.seatMovie.ScheduleView"
KORAIL_TICKETRESERVATION = f"{KORAIL_MOBILE}.certification.TicketReservation"
KORAIL_CANCEL = f"{KORAIL_MOBILE}.reservationCancel.ReservationCancelChk"
KORAIL_MYTICKETLIST = f"{KORAIL_MOBILE}.myTicket.MyTicketList"
KORAIL_MYTICKET_SEAT = f"{KORAIL_MOBILE}.refunds.SelTicketInfo"
KORAIL_MYRESERVATIONLIST = f"{KORAIL_MOBILE}.reservation.ReservationView"
KORAIL_CODE = f"{KORAIL_MOBILE}.common.code.do"

DYNAPATH_PATHS = (
    "/classes/com.korail.mobile.certification.TicketReservation",
    "/classes/com.korail.mobile.nonMember.NonMemTicket",
    "/classes/com.korail.mobile.seatMovie.ScheduleView",
    "/classes/com.korail.mobile.seatMovie.ScheduleViewSpecial",
    "/classes/com.korail.mobile.trn.prcFare.do",
    "/classes/com.korail.mobile.login.Login",
)

# Certificate DER from Korail 7.0.8 APK for synthetic Android ID generation
_SIGNING_CERTIFICATE = bytes.fromhex(
    "3082019b30820104a00302010202044cfa0d54300d06092a864886f70d01010505003011310f300d060355040313066b6f7261696c"
    "3020170d3130313230343039343334385a180f33303130303430363039343334385a3011310f300d060355040313066b6f7261696c"
    "30819f300d06092a864886f70d010101050003818d0030818902818100c3aa266fdb468cc4e9146fc299b776c683b99baae7fd231472"
    "0ce3c9b8d245b89ddd194c0140bf22001da468c601663d17a9646259c04cdda8e1a7af1e3c0f464bbd86ed316504a2f8cac9b3031d"
    "09f931d669bc5d53a8265f5272da75e1d31147902c89eff86553186ee8afc82d7cbefac3d864c351c8f9ede027aed488ab890203010001"
    "300d06092a864886f70d0101050500038181008b9b751dc8ee0a85b63f8ed8026d3d5b501e2cdc1905c27c69ad1af8e511a003dfe2b0"
    "1fd81b94ccce0b0d6951e6df864efda0406485fd56f49d2e22819c0d63cce9286481c3844c454ed34c5ce70a55bc62f69af5f753792"
    "e61227d8c397a20f42414ebc61773daa1c65c8bba0d7a2f7b7dcbdb92ed1c8d98a0f5eabe3076f2"
)


def generate_synthetic_android_id() -> str:
    """Generate a realistic 16-hex Android ID based on HMAC-SHA256 of the Korail 7.0.8 cert."""
    user_key = token_bytes(32)
    message = struct.pack(">I", len(_SIGNING_CERTIFICATE)) + _SIGNING_CERTIFICATE
    return hmac.new(user_key, message, hashlib.sha256).hexdigest()[:16]


class DynaPathV103Engine:
    """DynaPath v1.0.3 token generator matching Korail+ 7.0.8 behavior."""
    TABLE = "3FE9jgRD4KdCyuawklqGJYmvfMn15P7US8XbxeLQtWT6OicBAopINs2Vh0HZrz"
    SDK_VERSION = "v1.0.3"
    OS_VERSION = "14"
    OS_TYPE = "Android"
    DEVICE_MODEL = "SM-S928N"
    APP_ID = "com.korail.talk"
    AS_VALUE = "%5B38ff229cb34c7dda8e28220a2d750cce%5D"

    def __init__(self):
        self.app_start_ts = int(time.time() * 1000)
        self.last_ts = self.app_start_ts

    def _encode_bytes(self, s: str) -> list:
        out = []
        for ch in s:
            cp = ord(ch)
            if cp < 128:
                out.append(cp)
            elif cp < 2048:
                out.append(128 | ((cp >> 7) & 15))
                out.append(cp & 127)
            elif cp >= 262144:
                out.extend([160, (cp >> 14) & 127, (cp >> 7) & 127, cp & 127])
            elif (63488 & cp) != 55296:
                out.extend([((cp >> 14) & 15) | 144, (cp >> 7) & 127, cp & 127])
        return out

    def _make_key(self, key_str: str) -> int:
        big = 0
        for ch in key_str:
            cp = ord(ch)
            bit = 32768
            for _ in range(16):
                if bit & cp:
                    break
                bit >>= 1
            big = (big * (bit << 1)) + cp
        return big

    def _encode_table(self, num: int, size: int, base: str) -> str:
        sb = []
        cur = num
        for i in range(size):
            divisor = size - i
            rem = cur % divisor
            j8 = 0
            for k in range(len(base)):
                c = base[k]
                if c not in sb:
                    if j8 == rem:
                        sb.append(c)
                        break
                    j8 += 1
            cur //= divisor
        return "".join(sb)

    def _encode_normal_be(self, data_str: str, table: str) -> str:
        data = self._encode_bytes(data_str)
        sb = []
        i10 = 2
        i8 = 161
        i9 = 30
        idx = 0
        rem = len(data) % i10
        full = len(data) - rem
        i_arr = [0] * (i10 + 1)
        while idx < full:
            val = 0
            for _ in range(i10):
                val = (val * i8) + data[idx]
                idx += 1
            for i in range(i10 + 1):
                i_arr[i] = val % i9
                val //= i9
            for i in range(i10, -1, -1):
                sb.append(table[i_arr[i]])
        if rem > 0:
            val = 0
            for _ in range(rem):
                val = (val * i8) + data[idx]
                idx += 1
            for i in range(rem + 1):
                i_arr[i] = val % i9
                val //= i9
            while rem >= 0:
                sb.append(table[i_arr[rem]])
                rem -= 1
        return "".join(sb)

    def generate_token(self, device_id: str, ts: int, rand: str) -> str:
        rt = ts - self.last_ts
        self.last_ts = ts
        plaintext = (
            f"ai={self.APP_ID}&di={device_id}&as={self.AS_VALUE}&"
            f"su=false&dbg=false&emu=false&hk=false&it={self.app_start_ts}&"
            f"ts={ts}&rt={rt}&os={self.OS_VERSION}&dm={self.DEVICE_MODEL}&"
            f"st={self.OS_TYPE}&sv={self.SDK_VERSION}"
        )
        dyn_key = f"{self.SDK_VERSION}+{rand}+{ts}"
        key_enc = self._encode_normal_be(dyn_key, self.TABLE)
        big_key = self._make_key(dyn_key)
        custom_tbl = self._encode_table(big_key, 30, self.TABLE)
        body_enc = self._encode_normal_be(plaintext, custom_tbl)
        return f"bEeEP{self.TABLE[len(key_enc)]}{key_enc}{body_enc}"


class KorailClient708(BaseKorail):
    """Subclass of korail2.Korail implementing the Korail 7.0.8 API and DynaPath v1.0.3."""
    _device = "AD"
    _version = "250722001"
    _app_version = "7.0.8"
    _sid_key = b"2485dd54d9deaa36"
    _key = "korail1234567890"

    def __init__(self, korail_id: str, korail_pw: str, auto_login: bool = True, want_feedback: bool = False):
        self._session = requests.session()
        self._session.headers.update({
            "User-Agent": "korailtalk",
            "Accept-Encoding": "gzip",
            "Connection": "Keep-Alive",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        self._device_id = generate_synthetic_android_id()
        self._engine = DynaPathV103Engine()
        self.korail_id = korail_id
        self.korail_pw = korail_pw
        self.want_feedback = want_feedback
        self.logined = False
        self._idx = None

        if auto_login:
            self.login(korail_id, korail_pw)

    def _generate_sid(self, ts: int) -> str:
        plaintext = f"{self._device}{ts}".encode("utf-8")
        cipher = AES.new(self._sid_key, AES.MODE_CBC, iv=self._sid_key)
        return base64.b64encode(cipher.encrypt(pad(plaintext, 16))).decode("utf-8") + "\n"

    def _get_auth_headers_and_sid(self, url: str, include_sid: bool = True) -> Tuple[dict, Optional[str]]:
        headers = {
            "User-Agent": "korailtalk",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        sid = None
        if any(path in url for path in DYNAPATH_PATHS):
            ts = int(time.time() * 1000)
            rand = "".join(random.choices(string.ascii_letters + string.digits, k=4))
            token = self._engine.generate_token(self._device_id, ts, rand)
            headers["x-dynapath-m-token"] = token
            if include_sid:
                sid = self._generate_sid(ts)
        return headers, sid

    def _enc_password(self, password: str) -> Optional[str]:
        """Fetch 1-time password encryption key and encrypt with AES-CBC + double Base64."""
        r = self._session.post(KORAIL_CODE, data={"code": "app.login.cphd"})
        j = json.loads(r.text)
        if j.get("strResult") == "SUCC" and j.get("app.login.cphd"):
            self._idx = str(j["app.login.cphd"]["idx"])
            key = j["app.login.cphd"]["key"]
            encrypt_key = key.encode("utf-8")
            iv = key[:16].encode("utf-8")
            cipher = AES.new(encrypt_key, AES.MODE_CBC, iv)
            padded = pad(password.encode("utf-8"), AES.block_size)
            inner = base64.b64encode(cipher.encrypt(padded))
            outer = base64.urlsafe_b64encode(inner).decode("ascii")
            lines = [outer[i : i + 76] for i in range(0, len(outer), 76)]
            return "\n".join(lines) + "\n"
        return None

    def login(self, korail_id: Optional[str] = None, korail_pw: Optional[str] = None) -> bool:
        """Login with 7.0.8 payload specs."""
        if korail_id is not None:
            self.korail_id = korail_id
        if korail_pw is not None:
            self.korail_pw = korail_pw

        if not self.korail_id or not self.korail_pw:
            self.logined = False
            return False

        clean_id = str(self.korail_id).strip()
        if "@" in clean_id:
            input_flg = "5"
        elif clean_id.replace("-", "").isdigit() and len(clean_id.replace("-", "")) in (10, 11):
            input_flg = "4"
            clean_id = clean_id.replace("-", "")
        else:
            input_flg = "2"

        encrypted_pw = self._enc_password(self.korail_pw)
        if not encrypted_pw:
            logger.error("Failed to obtain encryption key for Korail login")
            self.logined = False
            return False

        headers, _ = self._get_auth_headers_and_sid(KORAIL_LOGIN, include_sid=False)
        data = {
            "Device": self._device,
            "Version": self._version,
            "AppVersion": self._app_version,
            "Key": self._key,
            "txtInputFlg": input_flg,
            "txtMemberNo": clean_id,
            "txtPwd": encrypted_pw,
            "checkValidPw": "Y",
            "idx": self._idx,
        }

        r = self._session.post(KORAIL_LOGIN, data=data, headers=headers)
        j = json.loads(r.text)

        if j.get("strResult") == "SUCC" and j.get("strMbCrdNo"):
            self._key = j.get("Key", self._key)
            self.membership_number = j.get("strMbCrdNo")
            self.name = j.get("strCustNm")
            self.email = j.get("strEmailAdr")
            self.logined = True
            return True
        else:
            self.logined = False
            msg_txt = j.get("h_msg_txt", "")
            msg_cd = j.get("h_msg_cd", "")
            logger.warning(f"Korail login rejected: [{msg_cd}] {msg_txt}")
            return False

    def search_train(
        self,
        dep: str,
        arr: str,
        date: Optional[str] = None,
        time_str: Optional[str] = None,
        train_type: TrainType = TrainType.ALL,
        passengers: Optional[List[Passenger]] = None,
        include_no_seats: bool = False,
        include_waiting_list: bool = False,
    ) -> List[Train]:
        """Search trains with 7.0.8 parameter specifications."""
        kst_now = datetime.utcnow() + timedelta(hours=9)
        if date is None:
            date = kst_now.strftime("%Y%m%d")
        if time_str is None:
            time_str = kst_now.strftime("%H%M%S")

        if passengers is None:
            passengers = [AdultPassenger()]

        passengers = Passenger.reduce(passengers)

        adult_count = reduce(lambda a, b: a + b.count, [x for x in passengers if isinstance(x, AdultPassenger)], 0)
        child_count = reduce(lambda a, b: a + b.count, [x for x in passengers if isinstance(x, ChildPassenger)], 0)
        toddler_count = reduce(lambda a, b: a + b.count, [x for x in passengers if isinstance(x, ToddlerPassenger)], 0)
        senior_count = reduce(lambda a, b: a + b.count, [x for x in passengers if isinstance(x, SeniorPassenger)], 0)

        url = KORAIL_SEARCH_SCHEDULE
        headers, _ = self._get_auth_headers_and_sid(url, include_sid=False)
        data = {
            "Device": self._device,
            "Version": self._version,
            "Sid": "",
            "txtMenuId": "11",
            "radJobId": "1",
            "selGoTrain": train_type,
            "txtTrnGpCd": train_type,
            "txtGoStart": dep,
            "txtGoEnd": arr,
            "txtGoAbrdDt": date,
            "txtGoHour": time_str,
            "txtPsgFlg_1": str(adult_count),
            "txtPsgFlg_2": str(child_count + toddler_count),
            "txtPsgFlg_3": str(senior_count),
            "txtPsgFlg_4": "0",
            "txtPsgFlg_5": "0",
            "txtSeatAttCd_2": "000",
            "txtSeatAttCd_3": "000",
            "txtSeatAttCd_4": "015",
            "ebizCrossCheck": "N",
            "srtCheckYn": "N",
            "rtYn": "N",
            "adjStnScdlOfrFlg": "N",
            "mbCrdNo": self.membership_number or "",
        }

        r = self._session.post(url, params=data, headers=headers)
        j = json.loads(r.text)

        if self._result_check(j):
            train_infos = j.get("trn_infos", {}).get("trn_info", [])
            trains = [Train(info) for info in train_infos]

            filter_fns = [lambda x: x.has_seat()]
            if include_no_seats:
                filter_fns.append(lambda x: not x.has_seat())
            if include_waiting_list:
                filter_fns.append(lambda x: x.has_waiting_list())

            trains = [t for t in trains if any(f(t) for f in filter_fns)]
            if not trains:
                raise NoResultsError()

            return trains

    def reserve(
        self,
        train: Train,
        passengers: Optional[List[Passenger]] = None,
        option: ReserveOption = ReserveOption.GENERAL_FIRST,
        try_waiting: bool = False,
    ) -> Optional[Reservation]:
        """Reserve train ticket with 7.0.8 compatibility."""
        reserving_seat = True
        seat_type = None
        try:
            if train.has_seat() is False:
                raise SoldOutError()
            elif option == ReserveOption.GENERAL_ONLY:
                if train.has_general_seat():
                    seat_type = "1"
                else:
                    raise SoldOutError()
            elif option == ReserveOption.SPECIAL_ONLY:
                if train.has_special_seat():
                    seat_type = "2"
                else:
                    raise SoldOutError()
            elif option == ReserveOption.GENERAL_FIRST:
                if train.has_general_seat():
                    seat_type = "1"
                else:
                    seat_type = "2"
            elif option == ReserveOption.SPECIAL_FIRST:
                if train.has_special_seat():
                    seat_type = "2"
                else:
                    seat_type = "1"
        except SoldOutError as e:
            if try_waiting and option != ReserveOption.SPECIAL_ONLY and train.has_general_waiting_list():
                reserving_seat = False
                seat_type = "1"
            else:
                raise e

        if passengers is None:
            passengers = [AdultPassenger()]

        passengers = Passenger.reduce(passengers)
        cnt = reduce(lambda x, y: x + y.count, passengers, 0)
        url = KORAIL_TICKETRESERVATION
        headers, _ = self._get_auth_headers_and_sid(url, include_sid=False)
        data = {
            "Device": self._device,
            "Version": self._version,
            "Key": self._key,
            "txtGdNo": "",
            "txtJobId": "1101" if reserving_seat else "1102",
            "txtTotPsgCnt": str(cnt),
            "txtSeatAttCd1": "000",
            "txtSeatAttCd2": "000",
            "txtSeatAttCd3": "000",
            "txtSeatAttCd4": "015",
            "txtSeatAttCd5": "000",
            "hidFreeFlg": "N",
            "txtStndFlg": "N",
            "txtMenuId": "11",
            "txtSrcarCnt": "0",
            "txtJrnyCnt": "1",
            "txtJrnySqno1": "001",
            "txtJrnyTpCd1": "11",
            "txtDptDt1": train.dep_date,
            "txtDptRsStnCd1": train.dep_code,
            "txtDptTm1": train.dep_time,
            "txtArvRsStnCd1": train.arr_code,
            "txtTrnNo1": train.train_no,
            "txtRunDt1": train.run_date,
            "txtTrnClsfCd1": train.train_type,
            "txtPsrmClCd1": seat_type,
            "txtTrnGpCd1": train.train_group,
            "txtChgFlg1": "",
            "txtJrnySqno2": "",
            "txtJrnyTpCd2": "",
            "txtDptDt2": "",
            "txtDptRsStnCd2": "",
            "txtDptTm2": "",
            "txtArvRsStnCd2": "",
            "txtTrnNo2": "",
            "txtRunDt2": "",
            "txtTrnClsfCd2": "",
            "txtPsrmClCd2": "",
            "txtChgFlg2": "",
        }

        for idx, psg in enumerate(passengers, 1):
            data.update(psg.get_dict(idx))

        r = self._session.get(url, params=data, headers=headers)
        j = json.loads(r.text)
        if self._result_check(j):
            rsv_id = j.get("h_pnr_no")
            if rsv_id:
                rsvlist = [x for x in self.reservations() if x.rsv_id == rsv_id]
                if len(rsvlist) == 1:
                    return rsvlist[0]
            return None
