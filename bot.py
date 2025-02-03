import pyautogui
import time
import utils

from exceptions import (
    NoCustomColorsError,
    NoCanvasError,
    NoPaletteError
)
from PIL import Image

class Palette:
    def __init__(self, colors_pos=None, box=None, rows=None, columns=None):
        if colors_pos is not None:
            self.colors_pos = colors_pos
            self.colors = colors_pos.keys()
            return
        
        self.box = box
        self.rows = rows
        self.columns = columns

        self._csizex = int(box[2] // columns)
        self._csizey = int(box[3] // rows)

        pix = pyautogui.screenshot(region=box).load()
        x, y = self._csizex // 2, self._csizey // 2
        xend = columns * self._csizex

        # Obtain RGB values of palette colors along with their coordinates
        # COLOR LAYOUT    :    ((r, g, b) : (x, y))
        self.colors_pos = dict()
        self.colors = set()
        for i in range(columns * rows):
            col = (pix[x, y][:3])
            self.colors_pos[col] = (box[0] + x, box[1] + y)
            self.colors.add(col)

            x = (x + self._csizex) % xend
            y = y + (self._csizey if (i + 1) % columns == 0 else 0)

    def nearest_color(self, query):
        return min(self.colors, key=lambda color: Palette.dist(color, query))

    @staticmethod
    def dist(colx, coly):
        '''
        Returns the squared distance between two RGB triplets. Since finding the root of 
        the distances has no effect on the sorting order of the final distances, it has 
        been avoided altogether for the sake of performance
        '''
        return sum((s - q) ** 2 for s, q in zip(colx, coly))
    
class Bot:
    DELAY, STEP, ACCURACY = tuple(i for i in range(3))
    # RESOURCES = (
    #     'assets/palette.png',
    #     'assets/canvas.png',
    #     'assets/custom_cols_mspaint.png'
    # )
    
    SLOTTED = 'slotted'
    LAYERED = 'layered'

    IGNORE_WHITE = 1 << 0
    USE_CUSTOM_COLORS = 1 << 1

    def __init__(self, config_file='config.json'):
        self.terminate = False
        self.settings = [.05, 5, .9]
        self.progress = 0
        self.options = Bot.IGNORE_WHITE
        self.config_file = config_file

        pyautogui.PAUSE = 0.0

    def init_palette(self, colors_pos=None, prows=None, pcols=None, pbox=None) -> Palette:

        # pbox = pyautogui.locateOnScreen(Bot.RESOURCES[0], confidence=self.settings[Bot.CONF])
        
        # Previously, the pbox was located using screenshots, this functionality is being phased out in favour of another method.
        # pyautogui's box format is (topleftx, toplefty, width, height). Likewise, palette expects and operates on this legacy format.
        # Therefore, pbox must be adjusted to fit the required format

        try:
            if colors_pos is not None:
                self._palette = Palette(colors_pos=colors_pos)
            else:
                pbox = pbox[0], pbox[1], pbox[2] - pbox[0], pbox[3] - pbox[1] 
                self._palette = Palette(box=pbox, rows=prows, columns=pcols)
        except:
            raise NoPaletteError('Bot could not continue because palette is either missing or its dimensions are faulty.')

        return self._palette

    def init_canvas(self, cabox):
        # self._canvas = pyautogui.locateOnScreen(Bot.RESOURCES[1], confidence=self.settings[Bot.CONF])

        # Just like the old pbox, the bot works on the assumption that the canvas box is stored using the format
        # (topleftx, toplefty, width, height). Adjustments have been made below to conform with this standard.
        self._canvas = cabox[0], cabox[1], cabox[2] - cabox[0], cabox[3] - cabox[1]

    def init_custom_colors(self, ccbox):
        # self._custom_colors = pyautogui.locateOnScreen(Bot.RESOURCES[2], confidence=self.settings[Bot.CONF])
        self._custom_colors = ccbox[0], ccbox[1], ccbox[2] - ccbox[0], ccbox[3] - ccbox[1]
    
    # def test(self):
    #     box = self._canvas
    #     locs = [p for p in self._palette.colors_pos.values()] + [(box[0], box[1]), (box[0] + box[2], box[1] + box[3])]
    #     for l in locs:
    #         pyautogui.moveTo(l)
    #         time.sleep(.25)

    def calculate_drawing_time(self, cmap, use_custom_colors):
        """
        Calculate estimated drawing time in seconds based on:
        - Number of color changes
        - Number of lines to draw
        - Whether custom colors are used
        - Current delay setting
        """
        total_time = 0
        
        # Time for color changes
        num_colors = len(cmap)
        if use_custom_colors:
            # Time for custom color dialog (~1.5s per color for dialog navigation and input)
            total_time += num_colors * 1.5
        else:
            # Time for palette color selection (~0.45s per color for 3 clicks)
            total_time += num_colors * 0.45
            
        # Time for drawing lines
        for lines in cmap.values():
            # Each line requires:
            # - delay between lines
            # - time to move mouse to start (~0.1s)
            # - time to drag to end (delay duration)
            num_lines = len(lines)
            total_time += num_lines * (self.settings[Bot.DELAY] + 0.1 + self.settings[Bot.DELAY])
            
        return total_time

    def process(self, file, flags=0, mode=LAYERED):
        '''
        Processes the requested file as per the flags submitted and returns 
        a tuple containing:
        1. A table mapping each color to a list of lines that are to be drawn on the canvas
        2. Estimated drawing time in seconds
        Each line contains both starting and terminating coordinates.
        '''
        
        self.terminate = False
        step = int(self.settings[Bot.STEP])
        img = Image.open(file).convert('RGBA')

        try: 
            x, y, cw, ch = self._canvas
        except:
            raise NoCanvasError('Bot could not continue because canvas is not initialized')

        tw, th = tuple(int(p // step) for p in utils.adjusted_img_size(img, (cw, ch)))
        xo = x = x + ((cw - tw * step) // 2)    # Center the drawing correctly
        y += ((ch - th * step) // 2)
    
        img_small = img.resize((tw, th), resample=Image.NEAREST)
        pix = img_small.load()
        w, h = img_small.size
        size = w * h
        start = xo, y

        nearest_colors = dict()
        cmap = dict()
        color_freq = dict()  # Track frequency of each color

        col_freq = dict()
        table_lines = list()
        table_colors = list()

        old_col = None

        # Create interval size from normalized accuracy value
        # Also setting a lower bound value of 1 to prevent interval_size from reaching 0
        interval_size = max((1 - self.settings[Bot.ACCURACY]) * 255, 1)

        for i in range(h):
            if mode is Bot.LAYERED:
                table_lines.append(list())
                table_colors.append(set())

            for j in range(w): 
                r, g, b = pix[j, i][:3]
                col = near = (r, g, b)

                # DESIGNATING COLOR OF THE CURRENT PIXEL
                # Deciding what to do with new RGB triplet
                if (r, g, b) not in nearest_colors:
                    if flags & Bot.USE_CUSTOM_COLORS:
                        col = tuple(int(round(v / interval_size) * interval_size) for v in col)
                    else:
                        # Find the nearest color from the palette
                        col = self._palette.nearest_color((r, g, b))

                    # Save the nearest color for this RGB triplet to avoid recomputing it
                    nearest_colors[(r, g, b)] = col
                else:
                    col = nearest_colors[(r, g, b)]

                # Count frequency of each color
                if mode is Bot.SLOTTED:
                    color_freq[col] = color_freq.get(col, 0) + 1

                # DESIGNATING COLOR LINES
                # End brush stroke when...
                # 1. a new color is encountered 
                # 2. the brush is at the end of the row
                if j == w - 1 or (old_col != None and old_col != col):
                    end = (x, y)
                    if mode is Bot.SLOTTED and not (old_col == (255, 255, 255) and flags & Bot.IGNORE_WHITE):
                        lines = cmap.get(old_col, [])
                        lines.append((start, end))
                        cmap[old_col] = lines
                    if mode is Bot.LAYERED:
                        table_lines[i].append((old_col, (start, end)))
                        table_colors[i].add(old_col)
                        col_freq[old_col] = col_freq.get(old_col, 0) + end[0] - start[0] + 1
                    start = (xo, y + step) if j == w - 1 else (x + step, y)
                
                self.progress = 100 * (i * w + (j + 1)) / size

                old_col = col
                x += step

            x = xo
            y += step
        
        if mode is Bot.SLOTTED:
            # Sort colors by frequency (most used to least used)
            sorted_colors = sorted(color_freq.items(), key=lambda x: x[1], reverse=True)
            sorted_cmap = {}
            for color, _ in sorted_colors:
                if color in cmap:  # Only include colors that have lines to draw
                    sorted_cmap[color] = cmap[color]
            estimated_time = self.calculate_drawing_time(sorted_cmap, bool(flags & Bot.USE_CUSTOM_COLORS))
            return sorted_cmap, estimated_time

        # Sort colors in decreasing order of their frequency and maintain a height level index for each color
        col_freq = tuple(k for k, _ in sorted(col_freq.items(), key=lambda item : item[1], reverse=True))
        col_index = {col_freq[i]: i for i in range(len(col_freq))}     
    
        # This loop will attempt to merge lines in favour of reducing the number of brush strokes when drawing.
        # Lines of lower layer colors can be easily merged into fewer strokes since they will be repainted over
        # again by colors from a higher layer
        for idc, col in enumerate(col_freq):
            for idr, row in enumerate(table_lines):
                if col not in table_colors[idr] or (col == (255, 255, 255) and flags & Bot.IGNORE_WHITE):
                    continue

                start, end, exposed = None, None, False
                for idl, line in enumerate(row):                        
                    if idc <= col_index[line[0]]:
                        start = line[1][0] if start is None else start
                        end = line[1][1]
                        exposed = exposed or idc == col_index[line[0]]
                    if start is not None and (idc > col_index[line[0]] or idl == len(row) - 1):
                        if exposed:
                            lines = cmap.get(col, [])
                            lines.append((start, end))
                            cmap[col] = lines
                        start, exposed = None, False

        estimated_time = self.calculate_drawing_time(cmap, bool(flags & Bot.USE_CUSTOM_COLORS))
        return cmap, estimated_time

    def draw(self, cmap):
        '''
        Draws the image as per the coordinates of the processed cmap table.
        Depending upon the selection of colors used, the bot will choose
        from either the standard palette or custom color option accordingly.
        '''
        
        for c, lines in cmap.items():
            if c in self._palette.colors:
                pyautogui.click(self._palette.colors_pos[c], clicks=3, interval=.15)
            else:
                try:
                    cc_box = self._custom_colors
                    pyautogui.click( (cc_box[0] + cc_box[2] // 2, cc_box[1] + cc_box[3] // 2 ), clicks=3, interval=.15)
                except:
                    raise NoCustomColorsError('Bot could not continue because custom colors are not initialized') 
                pyautogui.press('tab', presses=7, interval=.05)
                for val in c:
                    numbers = (d for d in str(val))
                    for n in numbers:
                        pyautogui.press(str(n))
                    pyautogui.press('tab')
                pyautogui.press('tab')
                pyautogui.press('enter')
                pyautogui.PAUSE = 0.0

            for line in lines:
                if self.terminate:
                    pyautogui.mouseUp()
                    return False
            
                time.sleep(self.settings[Bot.DELAY])
                pyautogui.moveTo(line[0])
                pyautogui.dragTo(line[1], duration=Bot.DELAY)

        return True       
