#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-

import aiohttp
import asyncio
import json
import random
import time
import traceback

GROUP_CAPACITY = 6

GROUP_MAP = {
    '会员购': "64812738d0164cc8b437c31aea0b9e38",
    '催审': "c46b048a77244bb4995ca7bb449f9f62",
    '大会员': "7f2aa81d0cd8436982ae0599ad7bc686",
    '投稿': "5764e62b72924e8a9814b79cc62ae69c",
    '播放': "1c3530a7b8974b4eb212a61eeef82e68",
    '漫画': "576259b9c16845f4975d9bb0604c785f",
    '直播': "576259b9c16845f4975d9bb0604c785f",
    '社区': "2b174d08548646998af667147e30e296",
    '账号': "658c477284ad444d837eeaf3f64e025e",
}

IGNORE_KEYWORD = [
    "帮",
    "问题",
    "转接",
    "见谅",
    "抱歉",
    "咨询",
]


class Agent(object):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<Agent {self.name}>"


class Message(object):
    OUT = 0
    IN = 1

    def __init__(self, agent, message, direction=IN):
        assert direction in (self.IN, self.OUT)
        self.timestamp = int(time.time())
        self.agent = agent
        self.message = message
        self.direction = direction

    def __repr__(self):
        return f"<Message {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))} {'Recv' if self.direction == self.IN else 'Send'} {self.agent.name} \"{self.message}\">"


class Session(object):
    def __init__(self, sys_num="102d1b48515346ec8e9fb543b54ec454", group_id="658c477284ad444d837eeaf3f64e025e", http_proxy_getter=None, connect_callback=None, disconnect_callback=None, receive_callback=None):
        self.sys_num = sys_num
        self.group_id = group_id
        self.http_proxy_getter = http_proxy_getter
        self.connect_callback = connect_callback
        self.disconnect_callback = disconnect_callback
        self.receive_callback = receive_callback
        self.agent = Agent(name="?")
        self.inbox = asyncio.Queue()
        self.waiting = 0

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers={
            'Content-Type': "application/x-www-form-urlencoded; charset=UTF-8",
            'Host': "service.bilibili.com",
            'Origin': "https://service.bilibili.com",
            'Referer': f"https://service.bilibili.com/v2/chat/pc/index.html?sysNum={self.sys_num}&groupId={self.group_id}",
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36",
        }, timeout=aiohttp.ClientTimeout(total=10))
        self.http_proxy = await self.http_proxy_getter() if self.http_proxy_getter is not None else None
        user_init_response = await self._user_init()
        self.uid = user_init_response['uid']
        self.pid = user_init_response['pid']  # = sys_num
        self.cid = user_init_response['cid']
        user_chat_connect_response = await self._user_chat_connect()
        self.puid = user_chat_connect_response['puid']
        if user_chat_connect_response['status'] == 0:
            self.agent = Agent(name="?")
            self.waiting = user_chat_connect_response['count']
            print(f"排队等待: {self.waiting}")
        elif user_chat_connect_response['status'] == 1:
            self.agent = Agent(name=user_chat_connect_response['aname'])
            self.waiting = 0
            if self.connect_callback is not None:
                asyncio.create_task(self.connect_callback(self))
        else:
            print(f"未知的连接响应: {user_chat_connect_response}")
        self.__receive_daemon_task = asyncio.create_task(
            self._receive_daemon())
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.__receive_daemon_task.cancel()
        try:
            await self.__receive_daemon_task
        except asyncio.CancelledError:
            pass
        await self.session.close()

    # 初始化
    async def _user_init(self):
        async with self.session.post(
            url="https://service.bilibili.com/v2/chat/user/init.action",
            data=f"ack=1&sysNum={self.sys_num}&source=0&chooseAdminId=&tranFlag=0&groupId={self.group_id}&partnerId=&tel=&email=&uname=&visitTitle=&visitUrl=&face=&realname=&weibo=&weixin=&qq=&sex=&birthday=&remark=&params=&isReComment=1&customerFields=&visitStartTime=&multiParams=&summaryParams=&channelFlag=",
            proxy=self.http_proxy,
        ) as response:
            return await response.json(content_type=None)

    # 接入聊天
    async def _user_chat_connect(self):
        # {"puid":"01add1f06a45405eb57a01f32f6e06b8","pu":"https://ws.sobot.com/webchat6/webchat","queueFlag":null,"aname":"A313","aface":"https://new-service.biliapi.net/console/102d1b48515346ec8e9fb543b54ec454/userImage/20191218121608973.JPG","wslink.bak":[],"aid":"c7e3edbbcc5d42a78d00e0af5cd170a1","wslink.default":"","status":1}
        # 排队 {'queueDoc': '<p>排队中，您在队伍中的第1个</p>', 'puid': '0fe51cdca7644a019de652df8150d1ee', 'pu': 'https://ws.sobot.com/webchat5/webchat', 'queueFlag': None, 'count': 1, 'wslink.bak': [], 'wslink.default': '', 'status': 0}
        async with self.session.post(
            url="https://service.bilibili.com/v2/chat/user/chatconnect.action",
            data=f"sysNum={self.sys_num}&uid={self.uid}&chooseAdminId=&tranFlag=0&way=1&current=false&groupId={self.group_id}&keyword=&keywordId=&queueFlag=&transferType=&transferAction=",
            proxy=self.http_proxy,
        ) as response:
            return await response.json(content_type=None)

    # 拉取消息
    async def _user_msg(self):
        async with self.session.get(
            url=f"https://service.bilibili.com/v2/chat/user/msg.action?puid={self.puid}&uid={self.uid}&token={int(time.time() * 1000)}",
            proxy=self.http_proxy,
        ) as response:
            return await response.json(content_type=None)

    # 响应消息
    async def _user_msg_ack(self, content):
        async with self.session.post(
            url="https://service.bilibili.com/v2/chat/user/msg/ack.action",
            data=f"content={json.dumps(content)}&tnk={int(time.time() * 1000)}",
            proxy=self.http_proxy,
        ) as response:
            return await response.json(content_type=None)

    # 正在输入
    async def _user_input(self, content):
        async with self.session.post(
            url="https://service.bilibili.com/v2/chat/user/input.action",
            data=f"cid={self.cid}&uid={self.uid}&content={content}".encode(),
            proxy=self.http_proxy,
        ) as response:
            return await response.json(content_type=None)

    # 发送消息
    async def _user_chat_send(self, content):
        async with self.session.post(
            url="https://service.bilibili.com/v2/chat/user/chatsend.action",
            data=f"puid={self.puid}&cid={self.cid}&uid={self.uid}&content={content}".encode(
            ),
            proxy=self.http_proxy,
        ) as response:
            return await response.json(content_type=None)

    # 心跳?
    async def _user_msgt(self):
        async with self.session.post(
            url="https://service.bilibili.com/v2/chat/user/msgt.action",
            data=f"uid={self.uid}&pid={self.pid}",
            proxy=self.http_proxy,
        ) as response:
            return await response.json(content_type=None)

    # 接收任务守护协程
    async def _receive_daemon(self):
        retry = 0
        user_msg_ack_content = []
        user_msgt_counter = 0
        while True:
            try:
                user_msg_response = await self._user_msg()
                if user_msg_response:
                    for msg in user_msg_response:
                        msg = json.loads(msg)
                        if msg['type'] == 200:  # 排队后客服接入
                            # {'type': 200, 'aname': 'Z202', 'aface': 'https://new-service.biliapi.net/console/102d1b48515346ec8e9fb543b54ec454/userImage/20191215200300298.PNG', 'aid': '45c53c8b636646aa8eb2acf16c64add2', 'chatType': 0, 'ts': '2020-04-16 12:57:12', 't': 1587013032504, 'msgId': '2fce3ec33fdc452a994a5caff61495ca', 'serviceInfo': {'displayField': None, 'nickName': None, 'name': None, 'face': None, 'email': None, 'remark': None, 'qq': None, 'tel': None, 'phone': None, 'serviceNo': None, 'adminHelloWord': None, 'serviceOutDoc': None, 'serviceOutTime': None, 'serviceEndPushMsg': None}}
                            self.waiting = 0
                            self.agent = Agent(name=msg['aname'])
                            user_msg_ack_content.append({
                                'type': 300,
                                'utype': 0,
                                'msgId': msg['msgId'],
                            })
                            if self.connect_callback is not None:
                                asyncio.create_task(
                                    self.connect_callback(self))
                        elif msg['type'] == 201:  # 排队进度更新
                            # {'type': 201, 'count': 6, 'msgId': 'e92a9f89116c47db80f2f923af821805', 'queueDoc': '<p>排队中，您在队伍中的第1个</p>'}
                            self.waiting = msg['count']
                            # print(f"排队等待: {self.waiting}")
                            user_msg_ack_content.append({
                                'type': 300,
                                'utype': 0,
                                'msgId': msg['msgId'],
                            })
                        elif msg['type'] == 202:  # 常规消息
                            # {"type":202,"aname":"C204","aface":"https://new-service.biliapi.net/console/102d1b48515346ec8e9fb543b54ec454/userImage/20191216134608567.PNG","content":"您好，这里是人工客服哟~请问有什么可以帮到您哒 ？可以协助您解决问题哟(๑′ᴗ‵๑)~☆~","msgType":0,"realMsg":null,"cid":null,"uid":"05164a88881e4e319db01c3cc0f55cf4","docId":null,"msgId":"05164a88881e4e319db01c3cc0f55cf41587015355787","params":"","groupId":"2b174d08548646998af667147e30e296"}
                            self.agent = Agent(name=msg['aname'])
                            m = Message(
                                agent=self.agent, message=msg['content'], direction=Message.IN)
                            await self.inbox.put(m)
                            user_msg_ack_content.append({
                                'type': 300,
                                'utype': 0,
                                'cid': msg['cid'],
                                'uid': msg['uid'],
                                'msgId': msg['msgId'],
                            })
                        elif msg['type'] == 205:  # 心跳?
                            # {"type":205,"content":"1","msgId":"cd82160591fc4b71ba98ecbaab9490bd"}
                            user_msg_ack_content.append({
                                'type': 300,
                                'utype': 0,
                                'msgId': msg['msgId'],
                            })
                        elif msg['type'] == 210:  # 转接客服
                            # {"type":210,"id":"a7db61aadd384be1baca687e64e695f5","name":"C204","face":"https://new-service.biliapi.net/console/102d1b48515346ec8e9fb543b54ec454/userImage/20191216134608567.PNG","serviceInfo":{"displayField":null,"nickName":null,"name":null,"face":null,"email":null,"remark":null,"qq":null,"tel":null,"phone":null,"serviceNo":null,"adminHelloWord":null,"serviceOutDoc":null,"serviceOutTime":null,"serviceEndPushMsg":null},"msgId":"4686cad105a94d61a33757bbf3c1f1b8"}
                            self.agent = Agent(name=msg['name'])
                            user_msg_ack_content.append({
                                'type': 300,
                                'utype': 0,
                                'msgId': msg['msgId'],
                            })
                            if self.connect_callback is not None:
                                asyncio.create_task(
                                    self.connect_callback(self))
                        else:
                            print("未知的消息类型", msg)
                            # 长时间无消息? {'type': 204, 'status': 4, 'aname': 'A306', 'msgId': '6b6209daf9da49f6a2b74b8154e2f855', 'puid': '3011cbf61c2640509b7ee88793aa1fd1'}
                            # 客服主动断开? {'type': 209, 'aname': 'Z302', 'aid': '3ae57739b88944759cdee6b68b4f3973', 'cid': '492bbe54e29546d5808a4ae0a44ed067', 'isQuestionFlag': 0, 'msgId': '4882165b88fc47578eca121be9dc4dea'}
                        if self.receive_callback is not None:
                            asyncio.create_task(
                                self.receive_callback(self, msg))
                    if user_msg_ack_content:
                        await self._user_msg_ack(user_msg_ack_content)
                        user_msg_ack_content = []
                if user_msgt_counter > 10:
                    user_msgt_response = await self._user_msgt()
                    if user_msgt_response['ustatus'] == 0:
                        if self.disconnect_callback is not None:
                            asyncio.create_task(self.disconnect_callback(self))
                    # print("msgt", user_msgt_response)
                    user_msgt_counter = 0
                else:
                    user_msgt_counter += 1
                await asyncio.sleep(3)
                retry = 0
            except asyncio.CancelledError:
                if self.disconnect_callback is not None:
                    asyncio.create_task(self.disconnect_callback(self))
                break
            except:
                if retry == 5:
                    traceback.print_exc()
                    if self.disconnect_callback is not None:
                        asyncio.create_task(self.disconnect_callback(self))
                    print("超过最大重试次数")
                    return
                retry += 1
                self.http_proxy = await self.http_proxy_getter() if self.http_proxy_getter is not None else None
                await asyncio.sleep(5)

    # 接收消息
    def receive(self):
        msg = []
        while True:
            try:
                msg.append(self.inbox.get_nowait())
            except asyncio.QueueEmpty:
                return msg

    # 发送消息
    async def send(self, message):
        # await self._user_input("'".join(lazy_pinyin(message)))
        m = Message(agent=self.agent, message=message, direction=Message.OUT)
        await self._user_chat_send(message)
        print(m)
        return m


class Group(object):
    def __init__(self):
        self.sessions = set()
        self.last_message = None
        self.__forward_daemon_task = {}

    async def _forward_daemon(self, session):
        try:
            async with session:
                while True:
                    message = await session.inbox.get()
                    self.last_message = message
                    print(message)
                    for keyword in IGNORE_KEYWORD:
                        if keyword in message.message:
                            break
                    else:
                        await self.broadcast(f"{message.message}", include_session=self.filter_session(exclude_agents=[message.agent.name, "?"]))
        except asyncio.CancelledError:
            pass
        except:
            traceback.print_exc()
            self.kick(session)

    async def _connect_callback(self, session):
        for s in self.sessions:
            if session.agent.name == s.agent.name and session != s:
                break
        else:
            # await session.send(f"锵锵，欢迎加入客服酱群聊系统，快来和{len(self.agents)}位可爱的客服小姐姐一起聊天吧~")
            # await session.send(f"怎么回事，客服娘遇到客服娘了！")
            # await self.broadcast(f"{session.agent.name}已加入群聊", include_session=self.filter_session(exclude_agents=[session.agent.name, "?"]))
            return
        print(f"{session.agent.name}已在群聊中, 自动关闭会话")
        session.disconnect_callback = None
        self.kick(session)

    async def _disconnect_callback(self, session):
        if session.agent.name != "?":
            print(f"{session.agent.name}失去响应")
            # await self.broadcast(f"{session.agent.name}退出了群聊，QAQ", include_session=self.filter_session(exclude_agents=[session.agent.name, "?"]))
            pass
        self.kick(session)

    async def broadcast(self, message, include_session=[]):
        for session in self.sessions:
            if not include_session or session in include_session:
                asyncio.create_task(session.send(message))

    def invite(self, session):
        if session not in self.sessions:
            session.connect_callback = self._connect_callback
            session.disconnect_callback = self._disconnect_callback
            self.sessions.add(session)
            self.__forward_daemon_task[session] = asyncio.create_task(
                self._forward_daemon(session))
            return True
        else:
            return False

    def kick(self, session):
        if session in self.sessions:
            self.__forward_daemon_task[session].cancel()
            self.sessions.remove(session)
            return True
        else:
            return False

    def filter_session(self, exclude_agents=[]):
        filtered_sessions = []
        for s in self.sessions:
            if s.agent.name not in exclude_agents:
                filtered_sessions.append(s)
        return filtered_sessions

    @property
    def agents(self):
        return list(set(s.agent.name for s in self.sessions if s.agent.name != "?"))


async def get_proxy():
    return


async def bot(message):
    return


async def main():
    print(f"创建{GROUP_CAPACITY}人群聊")
    group_name_list = list(GROUP_MAP.keys())
    random.shuffle(group_name_list)
    global group
    group = Group()
    while True:
        if len(group.sessions) < GROUP_CAPACITY:
            group_name = group_name_list.pop(0)
            group_name_list.append(group_name)
            group_id = GROUP_MAP[group_name]
            print(f"邀请{group_name}组客服加入群聊")
            group.invite(Session(group_id=group_id,
                                 http_proxy_getter=get_proxy))
        await asyncio.sleep(2)
        if type(group.last_message) == Message and time.time() - group.last_message.timestamp >= 30:
            bot_response = await bot(group.last_message.message)
            if bot_response:
                await group.broadcast(bot_response)
            group.last_message = None

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
