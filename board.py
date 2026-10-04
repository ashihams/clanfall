import pygame as pg
import pygame.font

from settings import *
from game_hooks import hooks


# Board surface on screen to draw menus
class Board:

    def __init__(self, width: int, height: int, game):
        self.surface = pg.display.set_mode((width, height), 0, 32)
        pg.display.set_caption('Among Us')
        self.width = width
        self.height = height
        self.game = game
        self.intro_bg = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/back.png").convert_alpha(), (width, height))
        self.intro_bg2 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/back2.png").convert_alpha(), (width, height))
        self.intro_title = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/title.png").convert_alpha(), (int(width / 2), int(height * 0.2)))
        self.intro_menu1 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/freeplay.png").convert_alpha(), (int(width / 5), int(height * 0.1)))
        self.intro_menu2 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/online.png").convert_alpha(), (int(width / 5), int(height * 0.1)))
        self.intro_menu3 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/help.png").convert_alpha(), (int(width / 5), int(height * 0.1)))
        self.intro_menu4 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/credits.png").convert_alpha(), (int(width / 5), int(height * 0.1)))
        self.intro_menu5 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/quit.png").convert_alpha(), (int(width / 5), int(height * 0.1)))
        self.intro_color1 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/blue.png").convert_alpha(), (int(width / 4), int(height * 0.1)))
        self.intro_color2 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/green.png").convert_alpha(), (int(width / 4), int(height * 0.1)))
        self.intro_color3 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/yellow.png").convert_alpha(), (int(width / 4), int(height * 0.1)))
        self.intro_color4 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/red.png").convert_alpha(), (int(width / 4), int(height * 0.1)))
        self.intro_color5 = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/orange.png").convert_alpha(), (int(width / 4), int(height * 0.1)))
        self.intro_choosecolour = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/choosecolour.png").convert_alpha(), (int(width / 2), int(height * 0.1)))
        self.intro_return = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/return.png").convert_alpha(), (int(width / 4), int(height * 0.1)))
        self.intro_entername = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/entername.png").convert_alpha(), (int(width / 2), int(height * 0.1)))
        self.intro_enteraddress = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/enteraddress.png").convert_alpha(), (int(width / 2), int(height * 0.1)))
        self.intro_input = pg.transform.smoothscale(pg.image.load("Assets/Images/Menu/input.png").convert_alpha(), (int(width / 3), int(height * 0.2)))
        self.intro_help = []
        for i in range(0, 9):
            img = pygame.image.load('Assets/Images/help/'+'help'+str(i+1)+'.png').convert_alpha()
            self.intro_help.append(pg.transform.scale(img, (width, height)))
        self.intro_credits = pg.transform.scale(pg.image.load("Assets/Images/credits/credits.png").convert_alpha(), (width, height))
        
        self.menu_font = pg.font.Font(FONT, 35)
        self.bonus_font = pg.font.Font(FONT, 30)
        self.title_font = pg.font.Font(FONT, 90)
        self.game_over_font = pg.font.Font(FONT, 120)
        self.game_left_font = pg.font.Font(FONT, 75)

    # Draw Main Menu - Intro Menu
    def draw_menu(self, *args):
        self.surface.blit(self.intro_bg, (0, 0))
        self.surface.blit(self.intro_title, (self.width / 4, self.height * 0.1))
        self.surface.blit(self.intro_menu1, (self.width / 2.5, self.height * 0.39))
        self.surface.blit(self.intro_menu2, (self.width / 2.5, self.height * 0.51))
        self.surface.blit(self.intro_menu3, (self.width / 2.5, self.height * 0.63))
        self.surface.blit(self.intro_menu4, (self.width / 2.5, self.height * 0.75))
        self.surface.blit(self.intro_menu5, (self.width / 2.5, self.height * 0.87))

        for drawable in args:
            drawable.draw_on(self.surface)
        hud_surface = self.draw_night_coin_hud(hooks.token_balance, NIGHT_COLOR_COIN, 24)
        self.surface.blit(hud_surface, (self.width - hud_surface.get_width() - 20, 20))
        pg.display.update()

    # Draw Choose Color/Character Menu
    def draw_choose_character(self, *args):
        self.surface.blit(self.intro_bg2, (0, 0))
        self.surface.blit(self.intro_choosecolour, (self.width / 3.9, self.height * 0.05))
        self.surface.blit(self.intro_color4, (self.width / 2.6, self.height * 0.2))
        self.surface.blit(self.intro_color1, (self.width / 2.6, self.height * 0.33))
        self.surface.blit(self.intro_color5, (self.width / 2.6, self.height * 0.46))
        self.surface.blit(self.intro_color3, (self.width / 2.6, self.height * 0.59))
        self.surface.blit(self.intro_color2, (self.width / 2.6, self.height * 0.72))
        self.surface.blit(self.intro_return, (self.width / 2.6, self.height * 0.85))

        for drawable in args:
            drawable.draw_on(self.surface)
        hud_surface = self.draw_night_coin_hud(hooks.token_balance, NIGHT_COLOR_COIN, 24)
        self.surface.blit(hud_surface, (self.width - hud_surface.get_width() - 20, 20))
        pg.display.update()

    # Draw Gameover Menu
    def draw_game_over(self, scoreboard: list, message: str, *args):
        background = pg.image.load("Assets/Images/Alerts/victory.PNG")
        #self.surface.fill(background)
        self.surface.blit(background,(0,0))
        self.draw_text(self.surface, message, self.width / 2, self.height * 0.2, self.game_over_font)
        pos = 0.5
        for player in scoreboard:
            self.draw_text(self.surface, player[0], self.width / 3, self.height * pos, self.bonus_font)
            self.draw_text(self.surface, player[1], self.width * 2 / 3, self.height * pos, self.bonus_font)
            pos += 0.08
        for drawable in args:
            drawable.draw_on(self.surface)
        pg.display.update()
        
    def draw_game_over_imposter(self, scoreboard: list, message: str, *args):
        background = pg.image.load("Assets/Images/Alerts/defeat.PNG")
        #self.surface.fill(background)
        self.surface.blit(background,(0,0))
        self.draw_text(self.surface, message, self.width / 2, self.height * 0.2, self.game_over_font)
        pos = 0.5
        for player in scoreboard:
            self.draw_text(self.surface, player[0], self.width / 3, self.height * pos, self.bonus_font)
            self.draw_text(self.surface, player[1], self.width * 2 / 3, self.height * pos, self.bonus_font)
            pos += 0.08
        for drawable in args:
            drawable.draw_on(self.surface)
        pg.display.update()

    def draw_game_left(self, scoreboard: list, message: str, *args):
        background = (0, 0, 0)
        self.surface.fill(background)
        self.draw_text(self.surface, message, self.width / 2, self.height * 0.2, self.game_left_font)
        pos = 0.5
        for player in scoreboard:
            self.draw_text(self.surface, player[0], self.width / 3, self.height * pos, self.bonus_font)
            self.draw_text(self.surface, player[1], self.width * 2 / 3, self.height * pos, self.bonus_font)
            pos += 0.08
        for drawable in args:
            drawable.draw_on(self.surface)
        pg.display.update()

    #Draw Input Name field Menu
    def draw_input(self, word: str, x: int, y: int):
        self.surface.blit(self.intro_bg2, (0, 0))
        self.surface.blit(self.intro_entername, (self.width / 3.9, self.height * 0.05))
        self.surface.blit(self.intro_input, (self.width / 3.0, self.height * 0.4))
        text = self.menu_font.render("{}".format(word), True, MENU_FONT_COLOR)
        rect = text.get_rect()
        rect.center = x, y
        pg.display.update()
        return self.surface.blit(text, rect)
    
    def draw_input_address(self, word: str, x: int, y: int):
        self.surface.blit(self.intro_bg2, (0, 0))
        self.surface.blit(self.intro_enteraddress, (self.width / 3.9, self.height * 0.05))
        self.surface.blit(self.intro_input, (self.width / 3.0, self.height * 0.4))
        text = self.menu_font.render("{}".format(word), True, MENU_FONT_COLOR)
        rect = text.get_rect()
        rect.center = x, y
        pg.display.update()
        return self.surface.blit(text, rect)
        
    def draw_help(self, i):
        self.surface.blit(self.intro_help[i], (0, 0))
        pg.display.update()
        
    def draw_credits(self):
        self.surface.blit(self.intro_credits, (0, 0))
        pg.display.update()

    def draw_pause(self):
        self.draw_text(self.surface, "Paused", self.width / 2, self.height / 2, self.title_font)

    def draw_bots_left(self, left: int, text_size):
        self.bots_left_font = pg.font.Font(FONT, text_size)
        if self.game.gamemode == "Freeplay":
            self.draw_text(self.surface, "Bots Alive: {}".format(left), 60, 25, self.bots_left_font)
        elif self.game.gamemode == "Multiplayer":
            self.draw_text(self.surface, "PLYR Alive: {}".format(left), 60 , 25, self.bots_left_font)

    def draw_player_name(self, player_name, text_color, text_size):
        self.player_name_font = pg.font.Font(FONT, text_size)
        text_surface = self.player_name_font.render(player_name + " - Imposter", True, text_color)
        text_surface2 = self.player_name_font.render(player_name+ " - Crewmate", True, text_color)
        if self.game.player.imposter:
            return text_surface
        else:
            return text_surface2

    def draw_ejected_text(self, p):
        self.draw_text(self.surface, p + " was ejected", self.width/2, self.height/2, self.bonus_font)

    def draw_light_timer_text(self, left: int, text_color, text_size):
        timer_font = pg.font.Font(FONT, text_size)
        text_surface = timer_font.render("{} ".format(left), True, text_color)
        return text_surface

    def draw_kill_timer_text(self, left: int, text_color, text_size):
        timer_font = pg.font.Font(FONT, text_size)
        text_surface = timer_font.render("{} ".format(left), True, text_color)
        return text_surface

    def draw_reactor_timer_imposter_text(self, left: int, text_color, text_size):
        timer_font = pg.font.Font(FONT, text_size)
        text_surface = timer_font.render("{} ".format(left), True, text_color)
        return text_surface

    def draw_reactor_timer_text(self, left: int, text_color, text_size):
        timer_font = pg.font.Font(FONT, text_size)
        text_surface = timer_font.render("Reactor Meltdown in: {} ".format(left) + " secs", True, text_color)
        return text_surface

    def draw_meeting_timer_text(self, left: int, text_color, text_size):
        timer_font = pg.font.Font(FONT, text_size)
        text_surface = timer_font.render("Voting Ends in: {} ".format(left), True, text_color)
        return text_surface

    def draw_night_coin_hud(self, balance: int, text_color, text_size):
        timer_font = pg.font.Font(FONT, text_size)
        text_surface = timer_font.render(f"Night Coin: {balance} {NIGHT_COIN_SYMBOL}", True, text_color)
        return text_surface

    @staticmethod
    def draw_adds(surface, x, y, image, amount=1):
        for i in range(amount):
            img_rect = image.get_rect()
            img_rect.x = x + 30 * i
            img_rect.y = y
            surface.blit(image, img_rect)

    @staticmethod

    def draw_text(surface, text, x, y, font):
        if text is not None:
            text = font.render(text, True, MENU_FONT_COLOR)
            rect = text.get_rect()
            rect.center = x, y
            surface.blit(text, rect)
